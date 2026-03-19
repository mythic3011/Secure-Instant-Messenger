"""
tests/security/test_replay_attack.py — Security Test Case 1
Demonstrates that replayed ciphertext is correctly rejected.

Per project spec §8 item 9b: "At least 2 Security Test Cases to verify
whether your design can resist attacks."
"""

import base64
import os
import time
import pytest

import secrets

from client.crypto.session import (
    IdentityKeypair,
    DHKeypair,
    SessionKey,
    RatchetChain,
    derive_ratchet_chains,
    derive_session_key_as_initiator,
    derive_session_key_as_responder,
    build_and_encrypt,
    decrypt_envelope,
    ReplayProtector,
    ReplayError,
)
from cryptography.exceptions import InvalidTag


def _random_conv_id() -> str:
    """Generate a random conversation ID for tests."""
    return secrets.token_hex(16)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def alice_keys():
    return IdentityKeypair.generate(), DHKeypair.generate()

@pytest.fixture
def bob_keys():
    return IdentityKeypair.generate(), DHKeypair.generate()

@pytest.fixture
def session_keys(alice_keys, bob_keys):
    alice_id_kp, alice_dh_kp = alice_keys
    bob_id_kp,   bob_dh_kp   = bob_keys
    conv_id = _random_conv_id()

    alice_session, eph_pub, conv_dh_pub = derive_session_key_as_initiator(
        my_identity_kp        = alice_id_kp,
        my_dh_kp              = alice_dh_kp,
        peer_identity_pub_bytes = bob_id_kp.public_bytes(),
        peer_dh_pub_bytes     = bob_dh_kp.public_bytes(),
        my_user_id            = "alice",
        peer_user_id          = "bob",
        conversation_id       = conv_id,
    )
    bob_session = derive_session_key_as_responder(
        my_identity_kp        = bob_id_kp,
        my_dh_kp              = bob_dh_kp,
        peer_identity_pub_bytes = alice_id_kp.public_bytes(),
        peer_dh_pub_bytes     = alice_dh_kp.public_bytes(),
        eph_pub_bytes         = eph_pub,
        conv_dh_pub_bytes     = conv_dh_pub,
        my_user_id            = "bob",
        peer_user_id          = "alice",
        conversation_id       = conv_id,
    )
    alice_send, alice_recv = derive_ratchet_chains(alice_session.raw, initiator=True)
    bob_send,   bob_recv   = derive_ratchet_chains(bob_session.raw,   initiator=False)
    return alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv


# ── Security Test Case 1: Replay Attack ──────────────────────────────────────

class TestReplayAttack:
    """
    SECURITY TEST CASE 1 — Replay Attack Resistance

    Scenario:
      An attacker captures a valid encrypted message from Alice to Bob,
      then re-submits it to the server later.
      Bob's client must detect and reject the replayed ciphertext.

    Expected result: ReplayError raised on second delivery attempt.
    """

    def test_duplicate_message_id_rejected(self, session_keys):
        """Same message ID submitted twice must be rejected."""
        alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv = session_keys
        replay_protector = ReplayProtector()

        envelope = build_and_encrypt(
            send_chain      = alice_send,
            plaintext       = "Hello Bob, this is a secret.",
            sender_id       = "alice",
            recipient_id    = "bob",
            conversation_id = conv_id,
            counter         = 0,
            ttl_seconds     = None,
            sent_at         = int(time.time()),
        )

        # First delivery — should succeed
        plaintext = decrypt_envelope(
            recv_chain       = bob_recv,
            envelope         = envelope,
            replay_protector = replay_protector,
        )
        assert plaintext == "Hello Bob, this is a secret."

        # Second delivery (replay) — must be rejected
        with pytest.raises(ReplayError, match="Duplicate message ID"):
            decrypt_envelope(
                recv_chain       = bob_recv,
                envelope         = envelope,
                replay_protector = replay_protector,
            )

    def test_replayed_counter_rejected(self, session_keys):
        """
        A message with a counter that has already been seen
        (even with a different ID) must be rejected.
        """
        alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv = session_keys
        replay_protector = ReplayProtector()

        # Deliver message with counter=5 (skip 0-4 to set chain_index=5)
        env1 = build_and_encrypt(
            send_chain=alice_send, plaintext="message 1",
            sender_id="alice", recipient_id="bob",
            conversation_id=conv_id, counter=5,
            ttl_seconds=None, sent_at=int(time.time()),
        )
        decrypt_envelope(recv_chain=bob_recv, envelope=env1,
                         replay_protector=replay_protector)

        # Deliver many more to advance the window past replay threshold
        for i in range(6, 6 + 60):
            env = build_and_encrypt(
                send_chain=alice_send, plaintext=f"message {i}",
                sender_id="alice", recipient_id="bob",
                conversation_id=conv_id, counter=i,
                ttl_seconds=None, sent_at=int(time.time()),
            )
            decrypt_envelope(recv_chain=bob_recv, envelope=env,
                             replay_protector=replay_protector)

        # Now try to replay counter=5 (outside window) — must be rejected
        env_replay = build_and_encrypt(
            send_chain=alice_send, plaintext="replayed message",
            sender_id="alice", recipient_id="bob",
            conversation_id=conv_id, counter=5,
            ttl_seconds=None, sent_at=int(time.time()),
        )
        with pytest.raises(ReplayError, match="outside replay window"):
            decrypt_envelope(recv_chain=bob_recv, envelope=env_replay,
                             replay_protector=replay_protector)


# ── Security Test Case 2: Ciphertext Tampering ───────────────────────────────

class TestCiphertextTampering:
    """
    SECURITY TEST CASE 2 — Ciphertext and Metadata Integrity

    Scenario:
      The server (HbC) or a network attacker attempts to:
        (a) Modify the encrypted message body
        (b) Change the sender/recipient in the envelope
        (c) Alter the TTL to extend message lifetime
        (d) Change the message counter

      All must be detected via the AES-GCM authentication tag.

    Expected result: InvalidTag raised for all tampering attempts.
    """

    def _fresh_envelope(self, alice_send, conv_id, counter=0, ttl=None):
        return build_and_encrypt(
            send_chain=alice_send,
            plaintext="Sensitive message content.",
            sender_id="alice",
            recipient_id="bob",
            conversation_id=conv_id,
            counter=counter,
            ttl_seconds=ttl,
            sent_at=int(time.time()),
        )

    def test_tampered_ciphertext_rejected(self, session_keys):
        """Bit-flip in ciphertext must cause decryption failure."""
        alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv = session_keys
        env = self._fresh_envelope(alice_send, conv_id)

        # Flip a byte in the ciphertext
        ct = bytearray(base64.b64decode(env.ciphertext_b64))
        ct[0] ^= 0xFF
        env.ciphertext_b64 = base64.b64encode(bytes(ct)).decode()

        with pytest.raises(InvalidTag):
            decrypt_envelope(
                recv_chain=bob_recv,
                envelope=env,
                replay_protector=ReplayProtector(),
            )

    def test_tampered_sender_id_rejected(self, session_keys):
        """
        Changing sender_id in the envelope changes the AD,
        which invalidates the GCM tag.
        """
        alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv = session_keys
        env = self._fresh_envelope(alice_send, conv_id)

        env.sender_id = "mallory"   # attacker impersonates alice

        with pytest.raises(InvalidTag):
            decrypt_envelope(
                recv_chain=bob_recv,
                envelope=env,
                replay_protector=ReplayProtector(),
            )

    def test_tampered_ttl_rejected(self, session_keys):
        """
        TTL is in AD. Attacker cannot extend message lifetime
        without breaking the authentication tag.
        """
        alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv = session_keys
        env = self._fresh_envelope(alice_send, conv_id, ttl=30)

        env.ttl_seconds = 999999   # attacker tries to extend TTL

        with pytest.raises(InvalidTag):
            decrypt_envelope(
                recv_chain=bob_recv,
                envelope=env,
                replay_protector=ReplayProtector(),
            )

    def test_tampered_counter_rejected(self, session_keys):
        """Counter is in AD. Cannot be altered without breaking tag."""
        alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv = session_keys
        env = self._fresh_envelope(alice_send, conv_id, counter=10)

        env.counter = 999   # attacker changes counter

        with pytest.raises(InvalidTag):
            decrypt_envelope(
                recv_chain=bob_recv,
                envelope=env,
                replay_protector=ReplayProtector(),
            )

    def test_cross_conversation_rejected(self, session_keys):
        """
        A message encrypted for conversation A cannot be injected
        into conversation B (conversation_id is in AD).
        """
        alice_session, bob_session, conv_id, alice_send, alice_recv, bob_send, bob_recv = session_keys
        env = self._fresh_envelope(alice_send, conv_id)

        # Attacker changes conversation_id to redirect message
        env.conversation_id = "deadbeef12345678"

        with pytest.raises(InvalidTag):
            decrypt_envelope(
                recv_chain=bob_recv,
                envelope=env,
                replay_protector=ReplayProtector(),
            )


# ── Bonus: Session Key Derivation Correctness ─────────────────────────────────

class TestSessionKeyDerivation:
    """
    Verify both sides derive the same session key.
    If they don't, all decryption fails — catch early.
    """

    def test_both_sides_derive_same_key(self, session_keys):
        alice_session, bob_session, _, _, _, _, _ = session_keys
        assert alice_session.raw == bob_session.raw, (
            "Alice and Bob derived DIFFERENT session keys — protocol error!"
        )

    def test_different_conversations_different_keys(self, alice_keys, bob_keys):
        alice_id_kp, alice_dh_kp = alice_keys
        bob_id_kp,   bob_dh_kp   = bob_keys

        conv1 = _random_conv_id()
        conv2 = _random_conv_id()

        sess1, _, _ = derive_session_key_as_initiator(
            my_identity_kp=alice_id_kp, my_dh_kp=alice_dh_kp,
            peer_identity_pub_bytes=bob_id_kp.public_bytes(),
            peer_dh_pub_bytes=bob_dh_kp.public_bytes(),
            my_user_id="alice", peer_user_id="bob", conversation_id=conv1,
        )
        sess2, _, _ = derive_session_key_as_initiator(
            my_identity_kp=alice_id_kp, my_dh_kp=alice_dh_kp,
            peer_identity_pub_bytes=bob_id_kp.public_bytes(),
            peer_dh_pub_bytes=bob_dh_kp.public_bytes(),
            my_user_id="alice", peer_user_id="charlie", conversation_id=conv2,
        )

        assert sess1.raw != sess2.raw, (
            "Different conversations must produce different session keys!"
        )
