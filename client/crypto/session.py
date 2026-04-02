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
import binascii
import hashlib
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Final

from cryptography.exceptions import InvalidSignature, InvalidTag
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
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from shared.protocol import (
    KDF_INFO_PREFIX,
    KEY_BYTES,
    MAX_SKIP,
    NONCE_BYTES,
    REPLAY_WINDOW,
    MessageEnvelope,
    PublicKeyBundle,
)

log = logging.getLogger(__name__)

IDENTITY_PUB_KEY_LEN: Final[int] = 32
DH_PUB_KEY_LEN: Final[int] = 32
KEY_BUNDLE_SIG_LEN: Final[int] = 64
PEER_BUNDLE_BLOCKED_MESSAGE: Final[str] = (
    "Unable to verify peer key bundle. Session setup was blocked for your safety."
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
    public_key: Ed25519PublicKey

    @classmethod
    def generate(cls) -> IdentityKeypair:
        """Generate a new identity keypair using OS CSPRNG."""
        priv = Ed25519PrivateKey.generate()
        return cls(private_key=priv, public_key=priv.public_key())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> IdentityKeypair:
        priv = Ed25519PrivateKey.from_private_bytes(raw)
        return cls(private_key=priv, public_key=priv.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())

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
    public_key: X25519PublicKey

    @classmethod
    def generate(cls) -> DHKeypair:
        priv = X25519PrivateKey.generate()
        return cls(private_key=priv, public_key=priv.public_key())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> DHKeypair:
        priv = X25519PrivateKey.from_private_bytes(raw)
        return cls(private_key=priv, public_key=priv.public_key())

    def private_bytes(self) -> bytes:
        return self.private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())

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


def _peer_fingerprint(peer_pub: bytes) -> str:
    """Stable peer-key fingerprint for trust-state persistence."""
    digest = hashlib.sha256(peer_pub).hexdigest()
    return "  ".join(digest[i : i + 8] for i in range(0, 40, 8))


class SecurityError(Exception):
    """Base class for security-relevant client-side failures."""


class ReplayAttackError(SecurityError):
    """Raised when a message is detected as a replay or duplicate."""


class ReplayError(ReplayAttackError):
    """Backward-compatible replay rejection error."""


class IntegrityError(SecurityError):
    """Raised when a ciphertext or its authenticated metadata fails integrity checks."""


class LocalStorageSecurityError(SecurityError):
    """Raised when local encrypted storage cannot be used safely."""


class TrustStateError(SecurityError):
    """Raised when trust-state policy blocks a key transition."""


class InvalidPeerBundleError(SecurityError):
    """Raised when fetched peer bundle material cannot be trusted."""


class MalformedPeerBundleError(InvalidPeerBundleError):
    """Raised when a fetched peer bundle is missing, malformed, or length-invalid."""


class PeerBundleSignatureError(InvalidPeerBundleError):
    """Raised when a fetched peer bundle signature does not verify."""


@dataclass(frozen=True)
class VerifiedPeerBundle:
    """Immutable trusted view of fetched peer key material."""

    user_id: str
    username: str
    identity_pub: bytes
    dh_pub: bytes

    @property
    def fingerprint_input(self) -> bytes:
        return self.identity_pub


def _require_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise MalformedPeerBundleError(f"{field_name} must be a non-empty string")
    return value


def _decode_bundle_field(value: object, field_name: str, expected_len: int) -> bytes:
    encoded = _require_non_empty_string(value, field_name)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MalformedPeerBundleError(f"{field_name} is not valid base64") from exc
    if len(decoded) != expected_len:
        raise MalformedPeerBundleError(f"{field_name} has invalid decoded length")
    return decoded


def validate_and_decode_peer_bundle(bundle: PublicKeyBundle) -> VerifiedPeerBundle:
    """
    Convert untrusted wire bundle data into immutable verified key material.

    The signed payload is the raw-byte concatenation: identity_pub || dh_pub.
    """
    try:
        user_id = _require_non_empty_string(getattr(bundle, "user_id", None), "user_id")
        username = _require_non_empty_string(getattr(bundle, "username", None), "username")
        identity_pub = _decode_bundle_field(
            getattr(bundle, "identity_pub_b64", None),
            "identity_pub_b64",
            IDENTITY_PUB_KEY_LEN,
        )
        dh_pub = _decode_bundle_field(
            getattr(bundle, "dh_pub_b64", None),
            "dh_pub_b64",
            DH_PUB_KEY_LEN,
        )
        key_sig = _decode_bundle_field(
            getattr(bundle, "key_sig_b64", None),
            "key_sig_b64",
            KEY_BUNDLE_SIG_LEN,
        )
    except InvalidPeerBundleError:
        raise
    except Exception as exc:
        raise MalformedPeerBundleError("Peer bundle fields are malformed") from exc

    if not verify_key_bundle(identity_pub, dh_pub, key_sig):
        raise PeerBundleSignatureError("Peer bundle signature verification failed")

    return VerifiedPeerBundle(
        user_id=user_id,
        username=username,
        identity_pub=identity_pub,
        dh_pub=dh_pub,
    )


def check_and_update_verified_peer_bundle(
    cache: IdentityKeyCache, peer_id: str, verified_bundle: VerifiedPeerBundle
) -> bool:
    """Update identity trust state using only verified fetched bundle material."""
    return cache.check_and_update(peer_id, verified_bundle.fingerprint_input)


# ---------------------------------------------------------------------------
# Session establishment — 2-DH + HKDF
#
# Why 2-DH plus a symmetric ratchet, not X3DH or full Double Ratchet:
#   X3DH requires a server-managed one-time pre-key bundle with replenishment
#   logic. A full Double Ratchet requires DH-ratchet turns, skipped-message-key
#   persistence, and more complex recovery logic. This project keeps the
#   initial root-key establishment explainable with 2-DH, then derives
#   per-message symmetric keys using a one-way ratchet.
#   Trade-off: simpler than Signal while still providing per-message forward
#   secrecy for the message keys. It does not provide the same recovery and
#   post-compromise properties as a full Double Ratchet.
# ---------------------------------------------------------------------------


@dataclass
class SessionKey:
    """
    Derived AES-256 session key for a specific conversation.
    Stored locally after first derivation.
    """

    raw: bytes  # 32 bytes
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
    eph_pub_bytes: bytes,  # received in first message envelope
    conv_dh_pub_bytes: bytes,  # received in first message envelope (per-conv DH pub)
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
    eph_pub = X25519PublicKey.from_public_bytes(eph_pub_bytes)

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


def _hkdf_simple(key: bytes, info: bytes, length: int = KEY_BYTES) -> bytes:
    """Single-key HKDF derivation with no salt (used for ratchet steps)."""
    return HKDF(
        algorithm=SHA256(),
        length=length,
        salt=None,
        info=info,
    ).derive(key)


# ---------------------------------------------------------------------------
# Symmetric ratchet chain — per-message forward secrecy
#
# Each message gets a unique key derived from a chain key that advances
# after each use. Old chain keys are overwritten. Skipped keys are cached
# up to MAX_SKIP to handle out-of-order delivery.
# ---------------------------------------------------------------------------


@dataclass
class RatchetChain:
    """
    Symmetric ratchet chain for per-message forward secrecy.

    chain_key advances after each message; old values are not retained.
    skipped_keys caches keys for out-of-order messages (up to MAX_SKIP).
    """

    chain_key: bytes
    index: int = 0
    skipped_keys: dict = field(default_factory=dict)  # index → msg_key

    def advance(self) -> bytes:
        """Derive message_key, advance chain_key. Returns message_key."""
        msg_key = _hkdf_simple(self.chain_key, b"COMP3334-msg-key")
        next_chain = _hkdf_simple(self.chain_key, b"COMP3334-chain-advance")
        # Overwrite old chain_key — forward secrecy
        self.chain_key = next_chain
        self.index += 1
        return msg_key

    def advance_to(self, target: int) -> bytes:
        """
        Advance to target index, caching skipped message keys.
        Raises ValueError if target - current > MAX_SKIP.
        """
        if target < self.index:
            raise ValueError(f"Target index {target} is behind current {self.index}")
        if target - self.index > MAX_SKIP:
            raise ValueError(f"Too many skipped messages: target={target}, current={self.index}")
        while self.index < target:
            skipped_key = self.advance()
            self.skipped_keys[self.index - 1] = skipped_key
        return self.advance()

    def try_skipped(self, index: int) -> bytes | None:
        """Pop and return a previously cached skipped key, or None."""
        value = self.skipped_keys.pop(index, None)
        return value if isinstance(value, bytes) else None

    def as_dict(self) -> dict[str, object]:
        return {
            "chain_key_hex": self.chain_key.hex(),
            "index": self.index,
            "skipped_keys": {str(k): v.hex() for k, v in self.skipped_keys.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> RatchetChain:
        return cls(
            chain_key=bytes.fromhex(d["chain_key_hex"]),
            index=d["index"],
            skipped_keys={int(k): bytes.fromhex(v) for k, v in d.get("skipped_keys", {}).items()},
        )


def derive_ratchet_chains(root_key: bytes, initiator: bool) -> tuple[RatchetChain, RatchetChain]:
    """
    Derive send and receive ratchet chains from the root session key.

    Initiator's send_chain == Responder's recv_chain (and vice versa).
    """
    chain_a = _hkdf_simple(root_key, b"COMP3334-chain-a")
    chain_b = _hkdf_simple(root_key, b"COMP3334-chain-b")
    if initiator:
        return RatchetChain(chain_key=chain_a), RatchetChain(chain_key=chain_b)
    else:
        return RatchetChain(chain_key=chain_b), RatchetChain(chain_key=chain_a)


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
    nonce = os.urandom(NONCE_BYTES)  # 96-bit random nonce, OS CSPRNG
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


@dataclass
class TrustState:
    fingerprint: str
    verified: bool
    key_changed: bool


class KeyChangeWarning(TrustStateError):
    """
    Raised when a peer's identity key differs from the locally cached value.
    The caller must display a warning to the user and require re-verification.
    """

    def __init__(
        self,
        peer_id: str,
        cached: bytes,
        received: bytes,
        *,
        verified: bool = False,
    ):
        self.peer_id = peer_id
        self.cached = cached
        self.received = received
        self.verified = verified
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

    def __init__(
        self,
        store: dict[str, bytes] | None = None,
        trust_states: dict[str, TrustState] | None = None,
    ):
        self._store: dict[str, bytes] = store or {}
        self._trust_states: dict[str, TrustState] = trust_states or {}

    def check_and_update(self, peer_id: str, received_pub: bytes) -> bool:
        """
        Check received key against cache.
        If not seen before: cache it (first trust / TOFU).
        If seen and matches: OK.
        If seen and different:
          - verified contact: raise KeyChangeWarning and do not overwrite
          - unverified contact: update to the new key and flag key_changed

        Returns:
            bool: True if a warning should be shown to the user.
        """
        cached = self._store.get(peer_id)
        if cached is None:
            # First contact — Trust On First Use (TOFU)
            self._store[peer_id] = received_pub
            self._trust_states[peer_id] = TrustState(
                fingerprint=_peer_fingerprint(received_pub),
                verified=False,
                key_changed=False,
            )
            return False

        if cached == received_pub:
            return False

        state = self._trust_states.get(
            peer_id,
            TrustState(
                fingerprint=_peer_fingerprint(cached),
                verified=False,
                key_changed=False,
            ),
        )
        if state.verified:
            state.key_changed = True
            self._trust_states[peer_id] = state
            raise KeyChangeWarning(peer_id, cached, received_pub, verified=True)

        self._store[peer_id] = received_pub
        self._trust_states[peer_id] = TrustState(
            fingerprint=_peer_fingerprint(received_pub),
            verified=False,
            key_changed=True,
        )
        return True

    def mark_verified(self, peer_id: str, pub: bytes) -> None:
        """
        User has manually verified the fingerprint.
        Update cache to the new key (used after re-verification on key change).
        """
        self._store[peer_id] = pub
        self._trust_states[peer_id] = TrustState(
            fingerprint=_peer_fingerprint(pub),
            verified=True,
            key_changed=False,
        )

    def get(self, peer_id: str) -> bytes | None:
        return self._store.get(peer_id)

    def get_trust_state(self, peer_id: str) -> TrustState | None:
        return self._trust_states.get(peer_id)

    def as_dict(self) -> dict[str, dict[str, object]]:
        return {
            peer_id: {
                "pub_hex": pub.hex(),
                "fingerprint": state.fingerprint,
                "verified": state.verified,
                "key_changed": state.key_changed,
            }
            for peer_id, pub in self._store.items()
            for state in [self._trust_states.get(peer_id)]
            if state is not None
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> IdentityKeyCache:
        store: dict[str, bytes] = {}
        trust_states: dict[str, TrustState] = {}
        for peer_id, entry in raw.items():
            if isinstance(entry, str):
                # Backward compatibility: old format was {peer_id: hex_pub}
                pub = bytes.fromhex(entry)
                store[peer_id] = pub
                trust_states[peer_id] = TrustState(
                    fingerprint=_peer_fingerprint(pub),
                    verified=False,
                    key_changed=False,
                )
                continue

            if not isinstance(entry, dict):
                continue
            pub_hex = entry.get("pub_hex")
            if not isinstance(pub_hex, str):
                continue
            store[peer_id] = bytes.fromhex(pub_hex)
            trust_states[peer_id] = TrustState(
                fingerprint=str(entry.get("fingerprint", _peer_fingerprint(store[peer_id]))),
                verified=bool(entry.get("verified", False)),
                key_changed=bool(entry.get("key_changed", False)),
            )
        return cls(store=store, trust_states=trust_states)


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
            log.warning(
                "security_event type=replay_attack reason=duplicate_message_id message_id=%s",
                message_id,
            )
            raise ReplayError(f"Duplicate message ID: {message_id}")
        if counter <= self._max_counter - REPLAY_WINDOW:
            log.warning(
                (
                    "security_event type=replay_attack "
                    "reason=counter_outside_window "
                    "message_id=%s counter=%s max_seen=%s"
                ),
                message_id,
                counter,
                self._max_counter,
            )
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
            "seen_ids": list(self._seen_ids),
        }


# ---------------------------------------------------------------------------
# High-level message helpers
# ---------------------------------------------------------------------------


def build_and_encrypt(
    *,
    send_chain: RatchetChain,
    plaintext: str,
    sender_id: str,
    recipient_id: str,
    conversation_id: str,
    counter: int,
    ttl_seconds: int | None,
    sent_at: int,
) -> MessageEnvelope:
    """
    Convenience wrapper: build a MessageEnvelope from plaintext using the ratchet chain.
    Advances the send chain and sets chain_index on the envelope.
    """
    from shared.protocol import MessageEnvelope

    # Advance ratchet to get a per-message key
    msg_key = send_chain.advance()
    chain_index = send_chain.index - 1  # index of the key just used

    # Build envelope skeleton first (needed for AD computation)
    env = MessageEnvelope(
        sender_id=sender_id,
        recipient_id=recipient_id,
        conversation_id=conversation_id,
        counter=counter,
        chain_index=chain_index,
        nonce_b64=base64.b64encode(os.urandom(NONCE_BYTES)).decode(),  # placeholder
        ciphertext_b64="",  # placeholder
        ttl_seconds=ttl_seconds,
        sent_at=sent_at,
    )

    ad = env.compute_ad()
    msg_sk = SessionKey(raw=msg_key, conversation_id=conversation_id, peer_id=recipient_id)
    ciphertext, nonce = encrypt_message(msg_sk, plaintext.encode("utf-8"), ad)

    env.nonce_b64 = base64.b64encode(nonce).decode()
    env.ciphertext_b64 = base64.b64encode(ciphertext).decode()
    return env


def decrypt_envelope(
    *,
    recv_chain: RatchetChain,
    envelope: MessageEnvelope,
    replay_protector: ReplayProtector,
) -> str:
    """
    Convenience wrapper: verify replay protection and decrypt a MessageEnvelope.
    Uses the ratchet recv_chain to derive the per-message key.

    Raises:
        ReplayError:  duplicate or replayed message
        IntegrityError: ciphertext or AD tampered with
        ValueError:   malformed fields (bad base64, etc.)
    """
    # Step 1: replay check (before touching crypto)
    replay_protector.check(envelope.id, envelope.counter)

    # Step 2: get per-message key from ratchet
    chain_index = envelope.chain_index
    msg_key = recv_chain.try_skipped(chain_index)
    if msg_key is None:
        msg_key = recv_chain.advance_to(chain_index)

    # Step 3: reconstruct AD and decrypt
    ad = envelope.compute_ad()
    nonce = base64.b64decode(envelope.nonce_b64)
    ciphertext = base64.b64decode(envelope.ciphertext_b64)

    msg_sk = SessionKey(
        raw=msg_key,
        conversation_id=envelope.conversation_id,
        peer_id=envelope.sender_id,
    )
    try:
        plaintext_bytes = decrypt_message(msg_sk, ciphertext, nonce, ad)
    except InvalidTag as exc:
        log.warning(
            "security_event type=integrity_error message_id=%s conversation_id=%s",
            envelope.id,
            envelope.conversation_id,
        )
        raise IntegrityError("Ciphertext or authenticated metadata was tampered with") from exc

    # Step 4: commit only after successful decryption
    replay_protector.commit(envelope.id, envelope.counter)

    return plaintext_bytes.decode("utf-8")
