"""
client/crypto/storage.py — Encrypted local storage for private keys and session state.

Security design:
  - User's login password is used to derive a storage key via Argon2id
  - All private key material is encrypted with AES-256-GCM before writing to disk
  - The server never sees the storage key or any plaintext private key
  - Storage file format: JSON with base64-encoded encrypted blobs

Layout of ~/.comp3334im/<username>/keystore.json:
  {
    "version": 1,
    "argon2_salt_b64": "<base64 16 bytes>",
    "argon2_time_cost": 3,
    "argon2_memory_cost": 65536,
    "argon2_parallelism": 1,
    "identity_priv_nonce_b64": "<base64 12 bytes>",
    "identity_priv_ct_b64":    "<base64 encrypted Ed25519 private key>",
    "dh_priv_nonce_b64":       "<base64 12 bytes>",
    "dh_priv_ct_b64":          "<base64 encrypted X25519 private key>",
    "identity_pub_b64":        "<base64 Ed25519 public key — stored plaintext>",
    "dh_pub_b64":              "<base64 X25519 public key — stored plaintext>",
    "key_sig_b64":             "<base64 Ed25519 sig over (identity_pub||dh_pub)>"
  }

Session cache (~/.comp3334im/<username>/sessions.json):
  {
    "<conversation_id>": {
      "session_key_nonce_b64": "...",
      "session_key_ct_b64":    "...",   # AES-GCM encrypted 32-byte session key
      "peer_id":               "...",
      "replay_state":          { "max_counter": N, "seen_ids": [...] },
      "identity_key_cache":    { "<peer_id>": "<hex pub key>" }
    }
  }

Cryptographic library: PyCA `cryptography` + argon2-cffi
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

import argon2.low_level as argon2_ll
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from client.crypto.session import (
    DHKeypair,
    IdentityKeyCache,
    IdentityKeypair,
    RatchetChain,
    ReplayProtector,
    SessionKey,
)
from shared.protocol import MAX_SESSION_MESSAGES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEYSTORE_VERSION    = 1
ARGON2_TIME_COST    = 3
ARGON2_MEMORY_COST  = 65536   # 64 MiB — memory-hard
ARGON2_PARALLELISM  = 1
ARGON2_HASH_LEN     = 32      # AES-256 key size
ARGON2_SALT_LEN     = 16
NONCE_BYTES         = 12      # AES-GCM nonce

_APP_DIR_NAME = ".comp3334im"


# ---------------------------------------------------------------------------
# Storage key derivation
# ---------------------------------------------------------------------------

def _derive_storage_key(password: str, salt: bytes) -> bytes:
    """
    Derive a 32-byte AES-256 storage key from the user's password using Argon2id.

    Argon2id parameters follow OWASP recommendations:
      time_cost=3, memory_cost=64MiB, parallelism=1
    The salt is stored alongside the ciphertext (not secret).
    """
    return argon2_ll.hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST,
        parallelism=ARGON2_PARALLELISM,
        hash_len=ARGON2_HASH_LEN,
        type=argon2_ll.Type.ID,
    )


def _aes_encrypt(key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    """Encrypt plaintext with AES-256-GCM. Returns (ciphertext_with_tag, nonce)."""
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return ct, nonce


def _aes_decrypt(key: bytes, ciphertext: bytes, nonce: bytes) -> bytes:
    """Decrypt AES-256-GCM ciphertext. Raises InvalidTag on failure."""
    return AESGCM(key).decrypt(nonce, ciphertext, None)


# ---------------------------------------------------------------------------
# App directory helpers
# ---------------------------------------------------------------------------

def _app_dir(username: str) -> Path:
    """Return (and create) the per-user app directory."""
    d = Path.home() / _APP_DIR_NAME / username
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Keystore — identity + DH private keys
# ---------------------------------------------------------------------------

@dataclass
class LocalKeys:
    identity_kp: IdentityKeypair
    dh_kp:       DHKeypair
    key_sig:     bytes   # Ed25519 sig over (identity_pub || dh_pub)


def save_keystore(username: str, password: str, keys: LocalKeys) -> None:
    """
    Encrypt and persist the user's private keys to disk.
    Called once at registration; never again unless keys are rotated.
    """
    salt = os.urandom(ARGON2_SALT_LEN)
    storage_key = _derive_storage_key(password, salt)

    id_ct, id_nonce = _aes_encrypt(storage_key, keys.identity_kp.private_bytes())
    dh_ct, dh_nonce = _aes_encrypt(storage_key, keys.dh_kp.private_bytes())

    data = {
        "version":                KEYSTORE_VERSION,
        "argon2_salt_b64":        base64.b64encode(salt).decode(),
        "argon2_time_cost":       ARGON2_TIME_COST,
        "argon2_memory_cost":     ARGON2_MEMORY_COST,
        "argon2_parallelism":     ARGON2_PARALLELISM,
        "identity_priv_nonce_b64": base64.b64encode(id_nonce).decode(),
        "identity_priv_ct_b64":   base64.b64encode(id_ct).decode(),
        "dh_priv_nonce_b64":      base64.b64encode(dh_nonce).decode(),
        "dh_priv_ct_b64":         base64.b64encode(dh_ct).decode(),
        # Public keys stored plaintext — they are not secret
        "identity_pub_b64":       keys.identity_kp.public_b64(),
        "dh_pub_b64":             keys.dh_kp.public_b64(),
        "key_sig_b64":            base64.b64encode(keys.key_sig).decode(),
    }

    path = _app_dir(username) / "keystore.json"
    path.write_text(json.dumps(data, indent=2))
    # Restrict file permissions: owner read/write only
    path.chmod(0o600)


def load_keystore(username: str, password: str) -> LocalKeys:
    """
    Load and decrypt the user's private keys from disk.

    Raises:
        FileNotFoundError: keystore does not exist (not registered on this device)
        ValueError:        wrong password (AES-GCM tag verification fails)
    """
    path = _app_dir(username) / "keystore.json"
    data = json.loads(path.read_text())

    salt        = base64.b64decode(data["argon2_salt_b64"])
    storage_key = _derive_storage_key(password, salt)

    try:
        id_priv_bytes = _aes_decrypt(
            storage_key,
            base64.b64decode(data["identity_priv_ct_b64"]),
            base64.b64decode(data["identity_priv_nonce_b64"]),
        )
        dh_priv_bytes = _aes_decrypt(
            storage_key,
            base64.b64decode(data["dh_priv_ct_b64"]),
            base64.b64decode(data["dh_priv_nonce_b64"]),
        )
    except Exception as exc:
        raise ValueError("Wrong password or corrupted keystore.") from exc

    return LocalKeys(
        identity_kp=IdentityKeypair.from_private_bytes(id_priv_bytes),
        dh_kp=DHKeypair.from_private_bytes(dh_priv_bytes),
        key_sig=base64.b64decode(data["key_sig_b64"]),
    )


def keystore_exists(username: str) -> bool:
    """Return True if a keystore file exists for this username on this device."""
    return (_app_dir(username) / "keystore.json").exists()


# ---------------------------------------------------------------------------
# Session cache — session keys + replay state + identity key cache
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    """
    Complete session state for a single conversation.

    SECURITY NOTE (session lifecycle):
      This state contains all cryptographic material for a conversation:
      - session_key: the root key (compromise exposes all messages)
      - send_chain/recv_chain: per-message ratchet keys
      - replay_protector: anti-replay state
      - identity_key_cache: peer identity verification state

      The messages_sent counter tracks how many messages have been encrypted
      under this session. When it reaches MAX_SESSION_MESSAGES, the caller
      MUST re-key by calling clear() and deriving a fresh session.
    """
    session_key:        SessionKey      # root key — kept for fingerprint/peer_id
    send_chain:         RatchetChain
    recv_chain:         RatchetChain
    replay_protector:   ReplayProtector
    identity_key_cache: IdentityKeyCache
    messages_sent:      int = 0         # counter for session message limit enforcement

    @property
    def needs_rekey(self) -> bool:
        """
        Return True if this session has reached the message limit.

        READ BEHAVIOR:
          The caller should check this before sending each message and
          trigger a re-key (new ephemeral DH exchange) when True.
        """
        return self.messages_sent >= MAX_SESSION_MESSAGES

    def clear(self) -> None:
        """
        Overwrite all key material in memory before session teardown.

        SECURITY NOTE (key cleanup):
          Call this when:
          1. The session is being re-keyed (needs_rekey is True)
          2. The user logs out
          3. The conversation is deleted

          This is best-effort due to Python's GC. See SessionKey.clear()
          for limitations.
        """
        self.session_key.clear()
        self.send_chain.clear()
        self.recv_chain.clear()
        # Reset counter
        self.messages_sent = 0


def save_sessions(
    username: str,
    password: str,
    states: dict[str, SessionState],
) -> None:
    """
    Encrypt and persist all session states to disk.
    Session keys are encrypted with the same storage key as the keystore.
    """
    path = _app_dir(username) / "keystore.json"
    data = json.loads(path.read_text())
    salt        = base64.b64decode(data["argon2_salt_b64"])
    storage_key = _derive_storage_key(password, salt)

    serialised: dict = {}
    for conv_id, state in states.items():
        sk_ct, sk_nonce = _aes_encrypt(storage_key, state.session_key.raw)
        serialised[conv_id] = {
            "session_key_nonce_b64": base64.b64encode(sk_nonce).decode(),
            "session_key_ct_b64":    base64.b64encode(sk_ct).decode(),
            "peer_id":               state.session_key.peer_id,
            "replay_state":          state.replay_protector.as_dict(),
            "send_chain":            state.send_chain.as_dict(),
            "recv_chain":            state.recv_chain.as_dict(),
            # Identity key cache: store as {peer_id: hex_pub}
            "identity_key_cache": {
                pid: pub.hex()
                for pid, pub in state.identity_key_cache.as_dict().items()
            },
            # Session lifecycle: persist message counter for re-key enforcement
            "messages_sent":         state.messages_sent,
        }

    sessions_path = _app_dir(username) / "sessions.json"
    sessions_path.write_text(json.dumps(serialised, indent=2))
    sessions_path.chmod(0o600)


def load_sessions(
    username: str,
    password: str,
) -> dict[str, SessionState]:
    """
    Load and decrypt all session states from disk.
    Returns empty dict if no sessions file exists yet.
    """
    sessions_path = _app_dir(username) / "sessions.json"
    if not sessions_path.exists():
        return {}

    keystore_path = _app_dir(username) / "keystore.json"
    ks_data     = json.loads(keystore_path.read_text())
    salt        = base64.b64decode(ks_data["argon2_salt_b64"])
    storage_key = _derive_storage_key(password, salt)

    raw = json.loads(sessions_path.read_text())
    result: dict[str, SessionState] = {}

    for conv_id, entry in raw.items():
        try:
            sk_raw = _aes_decrypt(
                storage_key,
                base64.b64decode(entry["session_key_ct_b64"]),
                base64.b64decode(entry["session_key_nonce_b64"]),
            )
        except Exception:
            # Corrupted entry — skip rather than crash
            continue

        result[conv_id] = SessionState(
            session_key=SessionKey(
                raw=sk_raw,
                conversation_id=conv_id,
                peer_id=entry["peer_id"],
            ),
            send_chain=RatchetChain.from_dict(entry["send_chain"]),
            recv_chain=RatchetChain.from_dict(entry["recv_chain"]),
            replay_protector=ReplayProtector(state=entry.get("replay_state")),
            identity_key_cache=IdentityKeyCache(
                store={
                    pid: bytes.fromhex(hex_pub)
                    for pid, hex_pub in entry.get("identity_key_cache", {}).items()
                }
            ),
            # Session lifecycle: restore message counter for re-key enforcement
            messages_sent=entry.get("messages_sent", 0),
        )

    return result


def delete_session(username: str, password: str, conversation_id: str) -> bool:
    """
    Delete a single session state with key cleanup.

    SECURITY NOTE (session deletion):
      Before removing from disk, we call clear() on the SessionState to
      overwrite key material in memory. This is best-effort — see
      SessionKey.clear() for Python GC limitations.

    Returns True if the session was found and deleted, False otherwise.
    """
    sessions_path = _app_dir(username) / "sessions.json"
    if not sessions_path.exists():
        return False

    raw = json.loads(sessions_path.read_text())
    if conversation_id not in raw:
        return False

    # Load and clear the session state before deletion
    keystore_path = _app_dir(username) / "keystore.json"
    ks_data     = json.loads(keystore_path.read_text())
    salt        = base64.b64decode(ks_data["argon2_salt_b64"])
    storage_key = _derive_storage_key(password, salt)

    entry = raw[conversation_id]
    try:
        sk_raw = _aes_decrypt(
            storage_key,
            base64.b64decode(entry["session_key_ct_b64"]),
            base64.b64decode(entry["session_key_nonce_b64"]),
        )
        state = SessionState(
            session_key=SessionKey(
                raw=sk_raw,
                conversation_id=conversation_id,
                peer_id=entry["peer_id"],
            ),
            send_chain=RatchetChain.from_dict(entry["send_chain"]),
            recv_chain=RatchetChain.from_dict(entry["recv_chain"]),
            replay_protector=ReplayProtector(state=entry.get("replay_state")),
            identity_key_cache=IdentityKeyCache(),
        )
        state.clear()  # overwrite key material in memory
    except Exception:
        pass  # if we can't decrypt, just delete the entry

    del raw[conversation_id]
    sessions_path.write_text(json.dumps(raw, indent=2))
    sessions_path.chmod(0o600)
    return True


def clear_all_sessions(username: str) -> None:
    """
    Delete all session state files for a user.

    SECURITY NOTE (logout cleanup):
      Call this on user logout to ensure no session keys persist on disk.
      The session keys in memory will be garbage-collected, but we cannot
      guarantee immediate zeroing. For higher assurance, use a language
      with manual memory management.
    """
    sessions_path = _app_dir(username) / "sessions.json"
    if sessions_path.exists():
        sessions_path.unlink()
