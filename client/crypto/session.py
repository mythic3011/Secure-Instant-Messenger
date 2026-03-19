"""
client/crypto/session.py — E2EE session establishment and message crypto.

Implements:
  - Ed25519 identity keypair generation and fingerprint
  - X25519 ECDH + HKDF-SHA256 session key derivation (2-DH protocol)
  - AES-256-GCM encrypt / decrypt with Associated Data
  - Key change detection
  - Replay protection (counter + message ID deduplication)

Cryptographic library: PyCA `cryptography` (well-reviewed, actively maintained)
  pip: cryptography>=42.0
  ref: https://cryptography.io/en/latest/

IMPORTANT: Never use this module's output as input to another cipher
           without re-reading the protocol spec in ARCHITECTURE.md §3.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)
from cryptography.exceptions import InvalidSignature, InvalidTag

from shared.protocol import (
    KDF_INFO_PREFIX,
    KEY_BYTES,
    NONCE_BYTES,
    REPLAY_WINDOW,
    MessageEnvelope,
)


# ---------------------------------------------------------------------------
# Identity keypair
# ---------------------------------------------------------------------------

@dataclass
class IdentityKeypair:
    """
    Long-term Ed25519 identity keypair.
    Private key NEVER leaves the client machine.
    Public key is uploaded to the server for others to verify.
    """
    private_key: Ed25519PrivateKey
    public_key:  Ed25519PublicKey

    @classmethod
    def generate(cls) -> "IdentityKeypair":
        """Generate a new identity keypair using OS CSPRNG."""
        priv = Ed25519PrivateKey.generate()
        return cls(private_key=priv, public_key=priv.public_key())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "IdentityKeypair":
        priv = Ed25519PrivateKey.from_private_bytes(raw)
        return cls(private_key=priv, public_key=priv.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )

    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    def public_b64(self) -> str:
        return base64.b64encode(self.public_bytes()).decode()

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)


@dataclass
class DHKeypair:
    """
    Long-term X25519 DH keypair.
    Used in session establishment (static-static DH component).
    Private key NEVER leaves the client machine.
    """
    private_key: X25519PrivateKey
    public_key:  X25519PublicKey

    @classmethod
    def generate(cls) -> "DHKeypair":
        priv = X25519PrivateKey.generate()
        return cls(private_key=priv, public_key=priv.public_key())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "DHKeypair":
        priv = X25519PrivateKey.from_private_bytes(raw)
        return cls(private_key=priv, public_key=priv.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )

    def public_bytes(self) -> bytes:
        return self.public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

    def public_b64(self) -> str:
        return base64.b64encode(self.public_bytes()).decode()


def make_key_signature(identity_kp: IdentityKeypair, dh_kp: DHKeypair) -> bytes:
    """
    Sign (identity_pub || dh_pub) with identity private key.
    Uploaded to server so others can verify key bundle ownership.
    Prevents server from swapping keys silently.
    """
    msg = identity_kp.public_bytes() + dh_kp.public_bytes()
    return identity_kp.sign(msg)


def verify_key_bundle(
    identity_pub_bytes: bytes,
    dh_pub_bytes: bytes,
    sig_bytes: bytes,
) -> bool:
    """
    Verify that a received public key bundle is self-consistent.
    Returns True if signature is valid, False otherwise.
    """
    try:
        pub = Ed25519PublicKey.from_public_bytes(identity_pub_bytes)
        pub.verify(sig_bytes, identity_pub_bytes + dh_pub_bytes)
        return True
    except InvalidSignature:
        return False


def compute_fingerprint(my_pub: bytes, their_pub: bytes) -> str:
    """
    Compute a human-readable safety number for key verification.
    Result is symmetric: same value on both sides of a conversation.

    Format: 5 groups of 8 hex chars
    Example: 3a8f1c2d 9b4e7a01 cc83f210 11de5509 ab002377
    """
    # Sort to make fingerprint symmetric
    keys = sorted([my_pub, their_pub])
    digest = hashlib.sha256(keys[0] + keys[1]).hexdigest()
    return "  ".join(digest[i : i + 8] for i in range(0, 40, 8))


# ---------------------------------------------------------------------------
# Session establishment — 2-DH + HKDF
#
# Why 2-DH and not X3DH or Double Ratchet:
#   X3DH requires a server-managed one-time pre-key bundle with replenishment
#   logic. Double Ratchet requires per-message ratchet state and out-of-order
#   message handling. Both add significant complexity. Our 2-DH (static-static
#   + ephemeral-static) provides forward secrecy for the initial exchange via
#   the ephemeral key, but reuses the session key for subsequent messages.
#   Trade-off: simpler implementation at the cost of no per-message forward
#   secrecy. If the session key leaks, all messages in that conversation are
#   exposed. This is documented in ARCHITECTURE.md §14.
# ---------------------------------------------------------------------------

@dataclass
class SessionKey:
    """
    Derived AES-256 session key for a specific conversation.
    Stored locally after first derivation.
    """
    raw: bytes   # 32 bytes
    conversation_id: str
    peer_id: str


def derive_session_key_as_initiator(
    *,
    my_identity_kp: IdentityKeypair,
    my_dh_kp: DHKeypair,
    peer_identity_pub_bytes: bytes,
    peer_dh_pub_bytes: bytes,
    my_user_id: str,
    peer_user_id: str,
    conversation_id: str,
) -> tuple[SessionKey, bytes, bytes]:
    """
    Derive a session key as the initiating party (Alice).

    Returns: (SessionKey, eph_pub_bytes, conv_dh_pub_bytes)
    Both eph_pub_bytes and conv_dh_pub_bytes MUST be sent in the first message
    envelope so the peer can recompute the same session key.

    Protocol:
        eph_priv, eph_pub     = X25519.generate()   # ephemeral key
        conv_dh_priv, conv_dh_pub = X25519.generate()  # per-conversation DH key
        DH1 = X25519(conv_dh_priv, peer_dh_pub)    # per-conv-static
        DH2 = X25519(eph_priv,     peer_dh_pub)    # ephemeral-static
        ikm = DH1 || DH2
        session_key = HKDF-SHA256(ikm, salt=KDF_SALT, info=INFO, length=32)
    """
    peer_dh_pub = X25519PublicKey.from_public_bytes(peer_dh_pub_bytes)

    # Ephemeral keypair — used once, generated with OS CSPRNG
    eph_kp = DHKeypair.generate()
    # Per-conversation DH keypair — fresh for each conversation (fixes Issue #2)
    conv_dh_kp = DHKeypair.generate()

    dh1 = conv_dh_kp.private_key.exchange(peer_dh_pub)
    dh2 = eph_kp.private_key.exchange(peer_dh_pub)
    ikm = dh1 + dh2

    raw_key = _hkdf_derive(ikm, my_user_id, peer_user_id, conversation_id)

    return (
        SessionKey(raw=raw_key, conversation_id=conversation_id, peer_id=peer_user_id),
        eph_kp.public_bytes(),
        conv_dh_kp.public_bytes(),
    )


def derive_session_key_as_responder(
    *,
    my_identity_kp: IdentityKeypair,
    my_dh_kp: DHKeypair,
    peer_identity_pub_bytes: bytes,
    peer_dh_pub_bytes: bytes,
    eph_pub_bytes: bytes,           # received in first message envelope
    conv_dh_pub_bytes: bytes,       # received in first message envelope (per-conv DH pub)
    my_user_id: str,
    peer_user_id: str,
    conversation_id: str,
) -> SessionKey:
    """
    Derive a session key as the responding party (Bob).

    Protocol mirrors initiator, using the received per-conversation DH and ephemeral keys:
        DH1 = X25519(my_dh_priv, conv_dh_pub)   # static-per-conv
        DH2 = X25519(my_dh_priv, eph_pub)        # static-ephemeral
        ikm = DH1 || DH2
        session_key = HKDF-SHA256(...)
    """
    conv_dh_pub = X25519PublicKey.from_public_bytes(conv_dh_pub_bytes)
    eph_pub     = X25519PublicKey.from_public_bytes(eph_pub_bytes)

    dh1 = my_dh_kp.private_key.exchange(conv_dh_pub)
    dh2 = my_dh_kp.private_key.exchange(eph_pub)
    ikm = dh1 + dh2

    raw_key = _hkdf_derive(ikm, peer_user_id, my_user_id, conversation_id)

    return SessionKey(raw=raw_key, conversation_id=conversation_id, peer_id=peer_user_id)


def _hkdf_derive(
    ikm: bytes,
    initiator_id: str,
    responder_id: str,
    conversation_id: str,
) -> bytes:
    """
    HKDF-SHA256 derivation.
    Salt and info are protocol constants — never empty.
    """
    # Salt is a fixed protocol constant — not secret, but ensures domain
    # separation from other HKDF uses. Using a non-empty salt is recommended
    # by RFC 5869 §3.1.
    salt = f"{KDF_INFO_PREFIX}-salt".encode()
    # Info binds the derived key to this specific conversation between these
    # specific users. Even if two conversations share the same DH output
    # (e.g., same static keys), the derived session keys will differ because
    # info differs. This prevents cross-conversation key reuse.
    info = f"{KDF_INFO_PREFIX}:{initiator_id}:{responder_id}:{conversation_id}".encode()

    return HKDF(
        algorithm=SHA256(),
        length=KEY_BYTES,
        salt=salt,
        info=info,
    ).derive(ikm)


# ---------------------------------------------------------------------------
# AES-256-GCM encrypt / decrypt
# ---------------------------------------------------------------------------

def encrypt_message(
    session_key: SessionKey,
    plaintext: bytes,
    ad: bytes,
) -> tuple[bytes, bytes]:
    """
    Encrypt plaintext with AES-256-GCM.

    Returns: (ciphertext_with_tag, nonce)
    - nonce is 12 random bytes from OS CSPRNG
    - ciphertext includes the 16-byte GCM authentication tag
    - AD is bound to the tag — tampering with AD causes decryption failure

    IMPORTANT: nonce must be included in the message envelope and must
               NEVER be reused with the same session_key.
               AES-GCM is catastrophically broken under nonce reuse.
    """
    nonce = os.urandom(NONCE_BYTES)   # 96-bit random nonce, OS CSPRNG
    aes_gcm = AESGCM(session_key.raw)
    ciphertext = aes_gcm.encrypt(nonce, plaintext, ad)
    return ciphertext, nonce


def decrypt_message(
    session_key: SessionKey,
    ciphertext: bytes,
    nonce: bytes,
    ad: bytes,
) -> bytes:
    """
    Decrypt and authenticate a message.

    Raises:
        InvalidTag: if ciphertext or AD has been tampered with,
                    or if the wrong session key is used.
                    Caller MUST discard the message on this exception.
    """
    aes_gcm = AESGCM(session_key.raw)
    # Raises cryptography.exceptions.InvalidTag on failure
    return aes_gcm.decrypt(nonce, ciphertext, ad)


# ---------------------------------------------------------------------------
# Key change detection
# ---------------------------------------------------------------------------

class KeyChangeWarning(Exception):
    """
    Raised when a peer's identity key differs from the locally cached value.
    The caller must display a warning to the user and require re-verification.
    """
    def __init__(self, peer_id: str, cached: bytes, received: bytes):
        self.peer_id  = peer_id
        self.cached   = cached
        self.received = received
        super().__init__(
            f"Identity key for '{peer_id}' has changed! "
            f"Old: {cached.hex()[:16]}… New: {received.hex()[:16]}…"
        )


class IdentityKeyCache:
    """
    Local cache of peer identity public keys.
    On every new session, compare received key with cached key.
    If different, raise KeyChangeWarning.

    Persisted to local encrypted storage between sessions.
    """

    def __init__(self, store: dict[str, bytes] | None = None):
        self._store: dict[str, bytes] = store or {}

    def check_and_update(self, peer_id: str, received_pub: bytes) -> None:
        """
        Check received key against cache.
        If not seen before: cache it (first trust / TOFU).
        If seen and matches: OK.
        If seen and different: raise KeyChangeWarning.
        """
        cached = self._store.get(peer_id)
        if cached is None:
            # First contact — Trust On First Use (TOFU)
            self._store[peer_id] = received_pub
        elif cached != received_pub:
            raise KeyChangeWarning(peer_id, cached, received_pub)
        # else: matches — no action needed

    def mark_verified(self, peer_id: str, pub: bytes) -> None:
        """
        User has manually verified the fingerprint.
        Update cache to the new key (used after re-verification on key change).
        """
        self._store[peer_id] = pub

    def get(self, peer_id: str) -> bytes | None:
        return self._store.get(peer_id)

    def as_dict(self) -> dict[str, bytes]:
        return dict(self._store)


# ---------------------------------------------------------------------------
# Replay protection
#
# Why replay window is 50:
#   The window must be large enough to tolerate out-of-order delivery
#   (e.g., messages arriving via offline queue in non-sequential order)
#   but small enough that an attacker cannot stockpile old messages for
#   delayed replay. 50 is a practical balance — Signal uses a similar
#   window size. The counter is also bound in AES-GCM AD, so forging
#   a counter value causes an InvalidTag error even if it passes the
#   window check.
# ---------------------------------------------------------------------------

class ReplayProtector:
    """
    Per-conversation replay and duplicate detection.

    Strategy:
      1. Track highest seen counter per sender.
         Reject counter ≤ (max_seen - REPLAY_WINDOW).
      2. Track message IDs in a sliding deque of size REPLAY_WINDOW * 2.
         Reject duplicate IDs.
      3. Counter is also bound in AES-GCM AD — forged counter causes InvalidTag.

    This class is NOT thread-safe. Protect with a lock in async context.
    """

    def __init__(self, state: dict | None = None):
        loaded = state or {}
        self._max_counter: int = loaded.get("max_counter", -1)
        self._seen_ids: deque[str] = deque(
            loaded.get("seen_ids", []),
            maxlen=REPLAY_WINDOW * 2,
        )

    def check(self, message_id: str, counter: int) -> None:
        """
        Check whether a message should be accepted.

        Raises:
            ReplayError: if the message is a replay or duplicate.

        Call this BEFORE attempting decryption.
        Call commit() AFTER successful decryption.
        """
        if message_id in self._seen_ids:
            raise ReplayError(f"Duplicate message ID: {message_id}")
        if counter <= self._max_counter - REPLAY_WINDOW:
            raise ReplayError(
                f"Counter {counter} is outside replay window "
                f"(max seen: {self._max_counter}, window: {REPLAY_WINDOW})"
            )

    def commit(self, message_id: str, counter: int) -> None:
        """
        Record a successfully decrypted message.
        Must be called after check() passes AND decryption succeeds.
        """
        self._seen_ids.append(message_id)
        if counter > self._max_counter:
            self._max_counter = counter

    def as_dict(self) -> dict:
        """Serialise state for persistent storage."""
        return {
            "max_counter": self._max_counter,
            "seen_ids":    list(self._seen_ids),
        }


class ReplayError(Exception):
    """Raised when a message is detected as a replay or duplicate."""
    pass


# ---------------------------------------------------------------------------
# High-level message helpers
# ---------------------------------------------------------------------------

def build_and_encrypt(
    *,
    session_key: SessionKey,
    plaintext: str,
    sender_id: str,
    recipient_id: str,
    conversation_id: str,
    counter: int,
    ttl_seconds: int | None,
    sent_at: int,
) -> MessageEnvelope:
    """
    Convenience wrapper: build a MessageEnvelope from plaintext.
    Handles nonce generation, AD construction, and base64 encoding.
    """
    from shared.protocol import MessageEnvelope
    import time

    # Build envelope skeleton first (needed for AD computation)
    env = MessageEnvelope(
        sender_id       = sender_id,
        recipient_id    = recipient_id,
        conversation_id = conversation_id,
        counter         = counter,
        nonce_b64       = base64.b64encode(os.urandom(NONCE_BYTES)).decode(),  # placeholder
        ciphertext_b64  = "",   # placeholder
        ttl_seconds     = ttl_seconds,
        sent_at         = sent_at,
    )

    ad = env.compute_ad()
    ciphertext, nonce = encrypt_message(
        session_key,
        plaintext.encode("utf-8"),
        ad,
    )

    env.nonce_b64      = base64.b64encode(nonce).decode()
    env.ciphertext_b64 = base64.b64encode(ciphertext).decode()
    return env


def decrypt_envelope(
    *,
    session_key: SessionKey,
    envelope: MessageEnvelope,
    replay_protector: ReplayProtector,
) -> str:
    """
    Convenience wrapper: verify replay protection and decrypt a MessageEnvelope.

    Raises:
        ReplayError:  duplicate or replayed message
        InvalidTag:   ciphertext or AD tampered with
        ValueError:   malformed fields (bad base64, etc.)
    """
    # Step 1: replay check (before touching crypto)
    replay_protector.check(envelope.id, envelope.counter)

    # Step 2: reconstruct AD and decrypt
    ad         = envelope.compute_ad()
    nonce      = base64.b64decode(envelope.nonce_b64)
    ciphertext = base64.b64decode(envelope.ciphertext_b64)

    plaintext_bytes = decrypt_message(session_key, ciphertext, nonce, ad)

    # Step 3: commit only after successful decryption
    replay_protector.commit(envelope.id, envelope.counter)

    return plaintext_bytes.decode("utf-8")
