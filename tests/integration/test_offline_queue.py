"""
tests/integration/test_offline_queue.py — Offline messaging (R20, R21, R22).

Scenario:
  1. Alice + Bob register and become friends.
  2. Bob is offline (no WebSocket).
  3. Alice sends encrypted message -> server stores in offline queue.
  4. Bob fetches via GET /v1/messages -> decrypts -> plaintext matches.
  5. Replaying same ciphertext -> server rejects (R22).
"""

from __future__ import annotations

import base64
import os
import time

import pyotp
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from client.crypto.session import (
    DHKeypair,
    IdentityKeypair,
    ReplayProtector,
    build_and_encrypt,
    decrypt_envelope,
    derive_ratchet_chains,
    derive_session_key_as_initiator,
    derive_session_key_as_responder,
    make_key_signature,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_client():
    import server.core.database as db_mod
    os.environ["TOKEN_SECRET_KEY"]        = "c" * 64
    os.environ["TOTP_ENCRYPTION_KEY"]     = "d" * 64
    os.environ["DATABASE_URL"]            = "sqlite+aiosqlite:////tmp/test_offline.db"
    os.environ["RATE_LIMIT_REGISTER_MAX"] = "1000"
    os.environ["RATE_LIMIT_LOGIN_MAX"]    = "1000"

    # Clean up any leftover DB from previous runs to avoid UNIQUE constraint violations
    try:
        os.unlink("/tmp/test_offline.db")
    except FileNotFoundError:
        pass

    from server.main import app
    await db_mod.init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db_mod.close_db()
    try:
        os.unlink("/tmp/test_offline.db")
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _register(client, username, password):
    id_kp = IdentityKeypair.generate()
    dh_kp = DHKeypair.generate()
    sig   = make_key_signature(id_kp, dh_kp)
    resp  = await client.post("/v1/auth/register", json={
        "username":         username,
        "password":         password,
        "identity_pub_b64": id_kp.public_b64(),
        "dh_pub_b64":       dh_kp.public_b64(),
        "key_sig_b64":      base64.b64encode(sig).decode(),
    })
    assert resp.status_code == 201, resp.text
    uri    = resp.json()["totp_provisioning_uri"]
    secret = dict(p.split("=", 1) for p in uri.split("?", 1)[1].split("&"))["secret"]
    return id_kp, dh_kp, secret

async def _login(client, username, password, secret):
    resp = await client.post("/v1/auth/login", json={
        "username":  username,
        "password":  password,
        "totp_code": pyotp.TOTP(secret).now(),
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Test 1: store-and-forward (R20)
# ---------------------------------------------------------------------------

async def test_offline_queue_store_and_forward(app_client):
    """Server queues ciphertext when Bob is offline; Bob decrypts on fetch."""
    pw = "OfflineTest_p4ss!"
    alice_id_kp, alice_dh_kp, a_sec = await _register(app_client, "off_alice", pw)
    bob_id_kp,   bob_dh_kp,   b_sec = await _register(app_client, "off_bob",   pw)
    a_tok = await _login(app_client, "off_alice", pw, a_sec)
    b_tok = await _login(app_client, "off_bob",   pw, b_sec)

    # Get user IDs and Bob's public keys
    r = await app_client.get("/v1/keys/off_alice", headers=_auth(a_tok))
    alice_id = r.json()["user_id"]
    r = await app_client.get("/v1/keys/off_bob", headers=_auth(a_tok))
    bob_id     = r.json()["user_id"]
    bob_dh_pub = base64.b64decode(r.json()["dh_pub_b64"])
    bob_id_pub = base64.b64decode(r.json()["identity_pub_b64"])

    # Become friends
    r = await app_client.post("/v1/friends/request",
        json={"recipient_username": "off_bob"}, headers=_auth(a_tok))
    assert r.status_code == 201, r.text
    req_id = r.json()["id"]
    r = await app_client.put(f"/v1/friends/request/{req_id}",
        json={"action": "accept"}, headers=_auth(b_tok))
    assert r.status_code in (200, 204), r.text

    # Alice derives session and encrypts
    # Get conversation ID from server (created on friend accept)
    r = await app_client.get("/v1/conversations", headers=_auth(a_tok))
    assert r.status_code == 200, r.text
    conv_id = r.json()["conversations"][0]["id"]
    alice_sess, eph_pub, conv_dh_pub = derive_session_key_as_initiator(
        my_identity_kp=alice_id_kp, my_dh_kp=alice_dh_kp,
        peer_identity_pub_bytes=bob_id_pub, peer_dh_pub_bytes=bob_dh_pub,
        my_user_id=alice_id, peer_user_id=bob_id, conversation_id=conv_id,
    )
    alice_send, _ = derive_ratchet_chains(alice_sess.raw, initiator=True)
    plaintext = "Hey Bob, you were offline!"
    env = build_and_encrypt(
        send_chain=alice_send, plaintext=plaintext,
        sender_id=alice_id, recipient_id=bob_id,
        conversation_id=conv_id, counter=0,
        ttl_seconds=None, sent_at=int(time.time()),
    )
    env.eph_pub_b64 = base64.b64encode(eph_pub).decode()
    env.conv_dh_pub_b64 = base64.b64encode(conv_dh_pub).decode()

    # Alice sends — Bob offline, should be queued (delivered_at = None)
    r = await app_client.post("/v1/messages",
        json={"envelope": env.model_dump()}, headers=_auth(a_tok))
    assert r.status_code == 201, r.text
    assert r.json()["delivered_at"] is None, "Must be undelivered while Bob offline"

    # Bob comes online and fetches
    r = await app_client.get("/v1/messages",
        params={"conversation_id": conv_id, "limit": 10},
        headers=_auth(b_tok))
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert len(msgs) == 1, f"Expected 1 queued message, got {len(msgs)}"
    assert msgs[0]["eph_pub_b64"] is not None

    # Bob derives session key and decrypts
    r = await app_client.get("/v1/keys/off_alice", headers=_auth(b_tok))
    bob_sess = derive_session_key_as_responder(
        my_identity_kp=bob_id_kp, my_dh_kp=bob_dh_kp,
        peer_identity_pub_bytes=base64.b64decode(r.json()["identity_pub_b64"]),
        peer_dh_pub_bytes=base64.b64decode(r.json()["dh_pub_b64"]),
        eph_pub_bytes=base64.b64decode(msgs[0]["eph_pub_b64"]),
        conv_dh_pub_bytes=base64.b64decode(msgs[0]["conv_dh_pub_b64"]),
        my_user_id=bob_id, peer_user_id=alice_id, conversation_id=conv_id,
    )
    _, bob_recv = derive_ratchet_chains(bob_sess.raw, initiator=False)
    from shared.protocol import MessageEnvelope
    decrypted = decrypt_envelope(
        recv_chain=bob_recv,
        envelope=MessageEnvelope.model_validate(msgs[0]),
        replay_protector=ReplayProtector(),
    )
    assert decrypted == plaintext


# ---------------------------------------------------------------------------
# Test 2: server-side replay rejection (R22)
# ---------------------------------------------------------------------------

async def test_offline_queue_replay_rejected(app_client):
    """Server rejects duplicate (conv_id, sender_id, counter) — UNIQUE constraint."""
    pw = "ReplayOff_p4ss!"
    alice_id_kp, alice_dh_kp, a_sec = await _register(app_client, "roff_alice", pw)
    bob_id_kp,   bob_dh_kp,   b_sec = await _register(app_client, "roff_bob",   pw)
    a_tok = await _login(app_client, "roff_alice", pw, a_sec)
    b_tok = await _login(app_client, "roff_bob",   pw, b_sec)

    r = await app_client.get("/v1/keys/roff_alice", headers=_auth(a_tok))
    alice_id = r.json()["user_id"]
    r = await app_client.get("/v1/keys/roff_bob", headers=_auth(a_tok))
    bob_id     = r.json()["user_id"]
    bob_dh_pub = base64.b64decode(r.json()["dh_pub_b64"])
    bob_id_pub = base64.b64decode(r.json()["identity_pub_b64"])

    r = await app_client.post("/v1/friends/request",
        json={"recipient_username": "roff_bob"}, headers=_auth(a_tok))
    req_id = r.json()["id"]
    await app_client.put(f"/v1/friends/request/{req_id}",
        json={"action": "accept"}, headers=_auth(b_tok))

    # Get conversation ID from server
    r = await app_client.get("/v1/conversations", headers=_auth(a_tok))
    assert r.status_code == 200, r.text
    conv_id = r.json()["conversations"][0]["id"]
    alice_sess, eph_pub, conv_dh_pub = derive_session_key_as_initiator(
        my_identity_kp=alice_id_kp, my_dh_kp=alice_dh_kp,
        peer_identity_pub_bytes=bob_id_pub, peer_dh_pub_bytes=bob_dh_pub,
        my_user_id=alice_id, peer_user_id=bob_id, conversation_id=conv_id,
    )
    alice_send, _ = derive_ratchet_chains(alice_sess.raw, initiator=True)
    env = build_and_encrypt(
        send_chain=alice_send, plaintext="replay test",
        sender_id=alice_id, recipient_id=bob_id,
        conversation_id=conv_id, counter=0,
        ttl_seconds=None, sent_at=int(time.time()),
    )
    env.eph_pub_b64 = base64.b64encode(eph_pub).decode()
    env.conv_dh_pub_b64 = base64.b64encode(conv_dh_pub).decode()

    # First send — OK
    r = await app_client.post("/v1/messages",
        json={"envelope": env.model_dump()}, headers=_auth(a_tok))
    assert r.status_code == 201, r.text

    # Replay — must be rejected
    r = await app_client.post("/v1/messages",
        json={"envelope": env.model_dump()}, headers=_auth(a_tok))
    assert r.status_code in (409, 422), (
        f"Expected 409/422 for replay, got {r.status_code}: {r.text}"
    )
