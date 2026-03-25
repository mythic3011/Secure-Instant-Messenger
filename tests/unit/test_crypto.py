"""
tests/unit/test_crypto.py — Unit tests for client/crypto/session.py
Uses the same cryptography library as Tutorial1.ipynb (PyCA cryptography).
"""

from __future__ import annotations

import os
import secrets

import pytest
from cryptography.exceptions import InvalidTag

from client.crypto.session import (
    DHKeypair,
    IdentityKeyCache,
    IdentityKeypair,
    IntegrityError,
    KeyChangeWarning,
    RatchetChain,
    ReplayError,
    ReplayProtector,
    SessionKey,
    TrustState,
    build_and_encrypt,
    compute_fingerprint,
    decrypt_envelope,
    decrypt_message,
    derive_ratchet_chains,
    derive_session_key_as_initiator,
    derive_session_key_as_responder,
    encrypt_message,
    make_key_signature,
    verify_key_bundle,
)


def _random_conv_id() -> str:
    """Generate a random conversation ID for tests."""
    return secrets.token_hex(16)


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
    assert (
        verify_key_bundle(id_kp.public_bytes(), dh_kp.public_bytes(), bad_sig) is False
    )


def test_key_bundle_wrong_key():
    id_kp = IdentityKeypair.generate()
    dh_kp = DHKeypair.generate()
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
    bob_id_kp = IdentityKeypair.generate()
    bob_dh_kp = DHKeypair.generate()
    conv_id = _random_conv_id()

    alice_sk, eph_pub, conv_dh_pub = derive_session_key_as_initiator(
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
        conv_dh_pub_bytes=conv_dh_pub,
        my_user_id=bob_id,
        peer_user_id=alice_id,
        conversation_id=conv_id,
    )

    alice_send, alice_recv = derive_ratchet_chains(alice_sk.raw, initiator=True)
    bob_send, bob_recv = derive_ratchet_chains(bob_sk.raw, initiator=False)

    return alice_sk, bob_sk, conv_id, alice_send, alice_recv, bob_send, bob_recv


def test_session_key_both_sides_match():
    alice_sk, bob_sk, _, _, _, _, _ = _make_session()
    assert alice_sk.raw == bob_sk.raw
    assert len(alice_sk.raw) == 32


def test_session_key_different_conversations():
    alice_sk1, _, _, _, _, _, _ = _make_session("alice", "bob")
    alice_sk2, _, _, _, _, _, _ = _make_session("alice", "carol")
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
    alice_sk, bob_sk, conv_id, alice_send, alice_recv, bob_send, bob_recv = (
        _make_session()
    )
    import time

    env = build_and_encrypt(
        send_chain=alice_send,
        plaintext="hello bob",
        sender_id="alice",
        recipient_id="bob",
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )

    rp = ReplayProtector()
    plaintext = decrypt_envelope(recv_chain=bob_recv, envelope=env, replay_protector=rp)
    assert plaintext == "hello bob"


def test_decrypt_envelope_replay_rejected():
    alice_sk, bob_sk, conv_id, alice_send, alice_recv, bob_send, bob_recv = (
        _make_session()
    )
    import time

    env = build_and_encrypt(
        send_chain=alice_send,
        plaintext="hello",
        sender_id="alice",
        recipient_id="bob",
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )

    rp = ReplayProtector()
    decrypt_envelope(recv_chain=bob_recv, envelope=env, replay_protector=rp)
    with pytest.raises(ReplayError):
        decrypt_envelope(recv_chain=bob_recv, envelope=env, replay_protector=rp)


# ---------------------------------------------------------------------------
# Ratchet chain tests
# ---------------------------------------------------------------------------


def test_ratchet_each_message_different_key():
    """Each message must use a different key — forward secrecy."""
    import time

    alice_sk, bob_sk, conv_id, alice_send, alice_recv, bob_send, bob_recv = (
        _make_session()
    )

    envs = []
    for i in range(3):
        env = build_and_encrypt(
            send_chain=alice_send,
            plaintext=f"message {i}",
            sender_id="alice",
            recipient_id="bob",
            conversation_id=conv_id,
            counter=i,
            ttl_seconds=None,
            sent_at=int(time.time()),
        )
        envs.append(env)

    # All nonces differ (different keys → different ciphertexts)
    nonces = [e.nonce_b64 for e in envs]
    assert len(set(nonces)) == 3, "Each message must use a unique nonce"

    # Bob can decrypt all in order
    rp = ReplayProtector()
    for i, env in enumerate(envs):
        pt = decrypt_envelope(recv_chain=bob_recv, envelope=env, replay_protector=rp)
        assert pt == f"message {i}"


def test_ratchet_out_of_order_within_window():
    """Out-of-order messages within MAX_SKIP must be decryptable."""
    import time

    alice_sk, bob_sk, conv_id, alice_send, alice_recv, bob_send, bob_recv = (
        _make_session()
    )

    # Alice sends 3 messages
    envs = []
    for i in range(3):
        env = build_and_encrypt(
            send_chain=alice_send,
            plaintext=f"msg {i}",
            sender_id="alice",
            recipient_id="bob",
            conversation_id=conv_id,
            counter=i,
            ttl_seconds=None,
            sent_at=int(time.time()),
        )
        envs.append(env)

    # Bob receives them out of order: 2, 0, 1
    rp = ReplayProtector()
    pt2 = decrypt_envelope(recv_chain=bob_recv, envelope=envs[2], replay_protector=rp)
    assert pt2 == "msg 2"
    pt0 = decrypt_envelope(recv_chain=bob_recv, envelope=envs[0], replay_protector=rp)
    assert pt0 == "msg 0"
    pt1 = decrypt_envelope(recv_chain=bob_recv, envelope=envs[1], replay_protector=rp)
    assert pt1 == "msg 1"


def test_ratchet_chain_serialise_roundtrip():
    """RatchetChain.as_dict / from_dict must preserve state."""
    chain = RatchetChain(chain_key=os.urandom(32))
    chain.advance()
    chain.advance()
    d = chain.as_dict()
    chain2 = RatchetChain.from_dict(d)
    assert chain2.chain_key == chain.chain_key
    assert chain2.index == chain.index


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
    assert cache.check_and_update("alice", pub2) is True


def test_identity_key_cache_mark_verified():
    cache = IdentityKeyCache()
    pub1 = os.urandom(32)
    pub2 = os.urandom(32)
    cache.check_and_update("alice", pub1)
    cache.mark_verified("alice", pub2)
    cache.check_and_update("alice", pub2)  # should not raise


def test_truststate_tofu_created_on_first_contact():
    cache = IdentityKeyCache()
    pub = os.urandom(32)

    warned = cache.check_and_update("alice", pub)

    assert warned is False
    assert cache.get("alice") == pub
    assert cache.get_trust_state("alice") == TrustState(
        fingerprint=cache.get_trust_state("alice").fingerprint,
        verified=False,
        key_changed=False,
    )


def test_truststate_mark_verified_persists_after_reload():
    cache = IdentityKeyCache()
    pub = os.urandom(32)
    cache.check_and_update("alice", pub)
    cache.mark_verified("alice", pub)

    restored = IdentityKeyCache.from_dict(cache.as_dict())
    trust = restored.get_trust_state("alice")

    assert trust is not None
    assert trust.verified is True
    assert trust.key_changed is False
    assert restored.get("alice") == pub


def test_verified_key_change_sets_key_changed_flag():
    cache = IdentityKeyCache()
    pub1 = os.urandom(32)
    pub2 = os.urandom(32)
    cache.check_and_update("alice", pub1)
    cache.mark_verified("alice", pub1)

    with pytest.raises(KeyChangeWarning):
        cache.check_and_update("alice", pub2)

    trust = cache.get_trust_state("alice")
    assert trust is not None
    assert trust.verified is True
    assert trust.key_changed is True


def test_verified_key_change_does_not_overwrite_fingerprint():
    cache = IdentityKeyCache()
    pub1 = os.urandom(32)
    pub2 = os.urandom(32)
    cache.check_and_update("alice", pub1)
    cache.mark_verified("alice", pub1)
    original = cache.get_trust_state("alice")

    with pytest.raises(KeyChangeWarning):
        cache.check_and_update("alice", pub2)

    updated = cache.get_trust_state("alice")
    assert updated is not None
    assert original is not None
    assert updated.fingerprint == original.fingerprint
    assert cache.get("alice") == pub1


def test_unverified_key_change_soft_warning_and_update_fingerprint():
    cache = IdentityKeyCache()
    pub1 = os.urandom(32)
    pub2 = os.urandom(32)
    cache.check_and_update("alice", pub1)
    original = cache.get_trust_state("alice")

    warned = cache.check_and_update("alice", pub2)
    updated = cache.get_trust_state("alice")

    assert warned is True
    assert original is not None
    assert updated is not None
    assert updated.verified is False
    assert updated.key_changed is True
    assert updated.fingerprint != original.fingerprint
    assert cache.get("alice") == pub2


def test_decrypt_envelope_tamper_raises_integrity_error():
    _, _, conv_id, alice_send, _, _, bob_recv = _make_session()
    import time

    env = build_and_encrypt(
        send_chain=alice_send,
        plaintext="hello",
        sender_id="alice",
        recipient_id="bob",
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )
    env.sender_id = "mallory"

    with pytest.raises(IntegrityError):
        decrypt_envelope(
            recv_chain=bob_recv,
            envelope=env,
            replay_protector=ReplayProtector(),
        )
