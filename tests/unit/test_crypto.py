"""
tests/unit/test_crypto.py — Unit tests for client/crypto/session.py
Uses the same cryptography library as Tutorial1.ipynb (PyCA cryptography).
"""

from __future__ import annotations

import base64
import os
import pytest

from cryptography.exceptions import InvalidTag

from client.crypto.session import (
    DHKeypair,
    IdentityKeyCache,
    IdentityKeypair,
    KeyChangeWarning,
    ReplayError,
    ReplayProtector,
    SessionKey,
    compute_fingerprint,
    decrypt_envelope,
    decrypt_message,
    derive_session_key_as_initiator,
    derive_session_key_as_responder,
    encrypt_message,
    make_key_signature,
    verify_key_bundle,
    build_and_encrypt,
)
from shared.protocol import MessageEnvelope, make_conversation_id


# ---------------------------------------------------------------------------
# Identity keypair
# ---------------------------------------------------------------------------

def test_identity_keypair_generate():
    kp = IdentityKeypair.generate()
    assert len(kp.public_bytes()) == 32
    assert len(kp.private_bytes()) == 32


def test_identity_keypair_roundtrip():
    kp = IdentityKeypair.generate()
    kp2 = IdentityKeypair.from_private_bytes(kp.private_bytes())
    assert kp.public_bytes() == kp2.public_bytes()


def test_key_bundle_signature_valid():
    id_kp = IdentityKeypair.generate()
    dh_kp = DHKeypair.generate()
    sig = make_key_signature(id_kp, dh_kp)
    assert verify_key_bundle(id_kp.public_bytes(), dh_kp.public_bytes(), sig) is True


def test_key_bundle_signature_invalid():
    id_kp = IdentityKeypair.generate()
    dh_kp = DHKeypair.generate()
    sig = make_key_signature(id_kp, dh_kp)
    # Tamper with signature
    bad_sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    assert verify_key_bundle(id_kp.public_bytes(), dh_kp.public_bytes(), bad_sig) is False


def test_key_bundle_wrong_key():
    id_kp  = IdentityKeypair.generate()
    dh_kp  = DHKeypair.generate()
    dh_kp2 = DHKeypair.generate()
    sig = make_key_signature(id_kp, dh_kp)
    # Sig is over dh_kp but we pass dh_kp2
    assert verify_key_bundle(id_kp.public_bytes(), dh_kp2.public_bytes(), sig) is False


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_symmetric():
    a = os.urandom(32)
    b = os.urandom(32)
    assert compute_fingerprint(a, b) == compute_fingerprint(b, a)


def test_fingerprint_format():
    a = os.urandom(32)
    b = os.urandom(32)
    fp = compute_fingerprint(a, b)
    # 5 groups of 8 hex chars separated by double spaces
    parts = fp.split("  ")
    assert len(parts) == 5
    for p in parts:
        assert len(p) == 8
        int(p, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# Session key derivation
# ---------------------------------------------------------------------------

def _make_session(alice_id="alice", bob_id="bob"):
    alice_id_kp = IdentityKeypair.generate()
    alice_dh_kp = DHKeypair.generate()
    bob_id_kp   = IdentityKeypair.generate()
    bob_dh_kp   = DHKeypair.generate()
    conv_id     = make_conversation_id(alice_id, bob_id)

    alice_sk, eph_pub = derive_session_key_as_initiator(
        my_identity_kp=alice_id_kp,
        my_dh_kp=alice_dh_kp,
        peer_identity_pub_bytes=bob_id_kp.public_bytes(),
        peer_dh_pub_bytes=bob_dh_kp.public_bytes(),
        my_user_id=alice_id,
        peer_user_id=bob_id,
        conversation_id=conv_id,
    )

    bob_sk = derive_session_key_as_responder(
        my_identity_kp=bob_id_kp,
        my_dh_kp=bob_dh_kp,
        peer_identity_pub_bytes=alice_id_kp.public_bytes(),
        peer_dh_pub_bytes=alice_dh_kp.public_bytes(),
        eph_pub_bytes=eph_pub,
        my_user_id=bob_id,
        peer_user_id=alice_id,
        conversation_id=conv_id,
    )

    return alice_sk, bob_sk, conv_id


def test_session_key_both_sides_match():
    alice_sk, bob_sk, _ = _make_session()
    assert alice_sk.raw == bob_sk.raw
    assert len(alice_sk.raw) == 32


def test_session_key_different_conversations():
    alice_sk1, _, _ = _make_session("alice", "bob")
    alice_sk2, _, _ = _make_session("alice", "carol")
    assert alice_sk1.raw != alice_sk2.raw


# ---------------------------------------------------------------------------
# AES-256-GCM encrypt / decrypt
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    sk = SessionKey(raw=os.urandom(32), conversation_id="c1", peer_id="bob")
    plaintext = b"hello world"
    ad = b"some associated data"
    ct, nonce = encrypt_message(sk, plaintext, ad)
    result = decrypt_message(sk, ct, nonce, ad)
    assert result == plaintext


def test_decrypt_tampered_ciphertext():
    sk = SessionKey(raw=os.urandom(32), conversation_id="c1", peer_id="bob")
    ct, nonce = encrypt_message(sk, b"secret", b"ad")
    bad_ct = bytes([ct[0] ^ 0xFF]) + ct[1:]
    with pytest.raises(InvalidTag):
        decrypt_message(sk, bad_ct, nonce, b"ad")


def test_decrypt_tampered_ad():
    sk = SessionKey(raw=os.urandom(32), conversation_id="c1", peer_id="bob")
    ct, nonce = encrypt_message(sk, b"secret", b"original_ad")
    with pytest.raises(InvalidTag):
        decrypt_message(sk, ct, nonce, b"tampered_ad")


def test_decrypt_wrong_key():
    sk1 = SessionKey(raw=os.urandom(32), conversation_id="c1", peer_id="bob")
    sk2 = SessionKey(raw=os.urandom(32), conversation_id="c1", peer_id="bob")
    ct, nonce = encrypt_message(sk1, b"secret", b"ad")
    with pytest.raises(InvalidTag):
        decrypt_message(sk2, ct, nonce, b"ad")


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------

def test_replay_protector_normal_flow():
    rp = ReplayProtector()
    rp.check("msg-1", 0)
    rp.commit("msg-1", 0)
    rp.check("msg-2", 1)
    rp.commit("msg-2", 1)


def test_replay_protector_duplicate_id():
    rp = ReplayProtector()
    rp.check("msg-1", 0)
    rp.commit("msg-1", 0)
    with pytest.raises(ReplayError):
        rp.check("msg-1", 1)  # same ID, different counter


def test_replay_protector_counter_outside_window():
    from shared.protocol import REPLAY_WINDOW
    rp = ReplayProtector()
    # Advance counter to REPLAY_WINDOW + 10
    for i in range(REPLAY_WINDOW + 10):
        rp.check(f"msg-{i}", i)
        rp.commit(f"msg-{i}", i)
    # Counter 0 is now outside the window
    with pytest.raises(ReplayError):
        rp.check("old-msg", 0)


def test_replay_protector_serialise_roundtrip():
    rp = ReplayProtector()
    rp.check("msg-1", 5)
    rp.commit("msg-1", 5)
    state = rp.as_dict()
    rp2 = ReplayProtector(state=state)
    with pytest.raises(ReplayError):
        rp2.check("msg-1", 5)  # duplicate


# ---------------------------------------------------------------------------
# Full encrypt/decrypt via build_and_encrypt / decrypt_envelope
# ---------------------------------------------------------------------------

def test_build_and_decrypt_envelope():
    alice_sk, bob_sk, conv_id = _make_session()
    import time

    env = build_and_encrypt(
        session_key=alice_sk,
        plaintext="hello bob",
        sender_id="alice",
        recipient_id="bob",
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )

    rp = ReplayProtector()
    plaintext = decrypt_envelope(session_key=bob_sk, envelope=env, replay_protector=rp)
    assert plaintext == "hello bob"


def test_decrypt_envelope_replay_rejected():
    alice_sk, bob_sk, conv_id = _make_session()
    import time

    env = build_and_encrypt(
        session_key=alice_sk,
        plaintext="hello",
        sender_id="alice",
        recipient_id="bob",
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )

    rp = ReplayProtector()
    decrypt_envelope(session_key=bob_sk, envelope=env, replay_protector=rp)
    with pytest.raises(ReplayError):
        decrypt_envelope(session_key=bob_sk, envelope=env, replay_protector=rp)


# ---------------------------------------------------------------------------
# Key change detection
# ---------------------------------------------------------------------------

def test_identity_key_cache_tofu():
    cache = IdentityKeyCache()
    pub = os.urandom(32)
    cache.check_and_update("alice", pub)  # first contact — no error
    cache.check_and_update("alice", pub)  # same key — no error


def test_identity_key_cache_change_raises():
    cache = IdentityKeyCache()
    pub1 = os.urandom(32)
    pub2 = os.urandom(32)
    cache.check_and_update("alice", pub1)
    with pytest.raises(KeyChangeWarning):
        cache.check_and_update("alice", pub2)


def test_identity_key_cache_mark_verified():
    cache = IdentityKeyCache()
    pub1 = os.urandom(32)
    pub2 = os.urandom(32)
    cache.check_and_update("alice", pub1)
    cache.mark_verified("alice", pub2)
    cache.check_and_update("alice", pub2)  # should not raise
