"""
tests/integration/test_e2e_message.py — End-to-end message flow test.

Tests the full path: register -> login -> friend request -> send message -> decrypt.
Uses httpx AsyncClient with the FastAPI app directly (no real server needed).
"""

from __future__ import annotations

import base64
import os
import time

import pyotp
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

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


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def app_client():
    """Spin up the FastAPI app with a temp in-memory DB."""
    import server.core.database as db_mod
    from server.core.config import clear_settings_cache

    # Patch settings to use temp SQLite and known secrets, with rate limiting disabled
    os.environ["TOKEN_SECRET_KEY"] = "a" * 64
    os.environ["TOTP_ENCRYPTION_KEY"] = "b" * 64
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/test_e2e.db"
    os.environ["RATE_LIMIT_REGISTER_MAX"] = "1000"
    os.environ["RATE_LIMIT_LOGIN_MAX"] = "1000"
    clear_settings_cache()

    # Clean up any leftover DB from previous runs to avoid UNIQUE constraint violations
    try:
        os.unlink("/tmp/test_e2e.db")
    except FileNotFoundError:
        pass

    from server.main import app

    await db_mod.init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await db_mod.close_db()
    clear_settings_cache()
    # Clean up test DB
    try:
        os.unlink("/tmp/test_e2e.db")
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_keypair():
    id_kp = IdentityKeypair.generate()
    dh_kp = DHKeypair.generate()
    sig = make_key_signature(id_kp, dh_kp)
    return id_kp, dh_kp, sig


async def _register(client: AsyncClient, username: str, password: str):
    id_kp, dh_kp, sig = _make_keypair()
    resp = await client.post(
        "/v1/auth/register",
        json={
            "username": username,
            "password": password,
            "identity_pub_b64": id_kp.public_b64(),
            "dh_pub_b64": dh_kp.public_b64(),
            "key_sig_b64": base64.b64encode(sig).decode(),
        },
    )
    assert resp.status_code == 201, resp.text
    totp_uri = resp.json()["totp_provisioning_uri"]
    # Extract secret from otpauth URI: otpauth://totp/...?secret=BASE32...
    secret = dict(p.split("=", 1) for p in totp_uri.split("?", 1)[1].split("&"))["secret"]
    return id_kp, dh_kp, secret


async def _login(client: AsyncClient, username: str, password: str, totp_secret: str) -> str:
    code = pyotp.TOTP(totp_secret).now()
    resp = await client.post(
        "/v1/auth/login",
        json={
            "username": username,
            "password": password,
            "totp_code": code,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.asyncio
async def test_full_e2e_message_flow(app_client: AsyncClient):
    """
    Alice registers, Bob registers.
    Alice sends Bob a friend request; Bob accepts.
    Alice fetches Bob's keys, derives session, encrypts a message.
    Bob fetches the message, derives session, decrypts — plaintext matches.
    Bob sends ACK; delivered_at is set.
    """
    client = app_client

    # 1. Register Alice and Bob
    alice_id_kp, alice_dh_kp, alice_totp = await _register(client, "alice_e2e", "AlicePassword123!")
    bob_id_kp, bob_dh_kp, bob_totp = await _register(client, "bob_e2e", "BobPassword123!")

    # 2. Login
    alice_token = await _login(client, "alice_e2e", "AlicePassword123!", alice_totp)
    bob_token = await _login(client, "bob_e2e", "BobPassword123!", bob_totp)

    # 3. Alice sends Bob a friend request; Bob accepts
    resp = await client.post(
        "/v1/friends/request",
        json={"recipient_username": "bob_e2e"},
        headers=_auth(alice_token),
    )
    assert resp.status_code == 201, resp.text
    request_id = resp.json()["id"]

    resp = await client.put(
        f"/v1/friends/request/{request_id}",
        json={"action": "accept"},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 204, resp.text

    # 4. Alice fetches Bob's public keys
    resp = await client.get("/v1/keys/bob_e2e", headers=_auth(alice_token))
    assert resp.status_code == 200, resp.text
    bob_bundle = resp.json()
    bob_user_id = bob_bundle["user_id"]

    # 5. Alice fetches her own user_id
    resp = await client.get("/v1/keys/alice_e2e", headers=_auth(alice_token))
    alice_user_id = resp.json()["user_id"]

    # 5b. Get conversation ID from server (created on friend accept)
    resp = await client.get("/v1/conversations", headers=_auth(alice_token))
    assert resp.status_code == 200, resp.text
    convs = resp.json()["conversations"]
    assert len(convs) >= 1, "Expected at least 1 conversation after friend accept"
    conv_id = convs[0]["id"]

    # 6. Alice derives session key as initiator
    alice_sk, eph_pub_bytes, conv_dh_pub_bytes = derive_session_key_as_initiator(
        my_identity_kp=alice_id_kp,
        my_dh_kp=alice_dh_kp,
        peer_identity_pub_bytes=base64.b64decode(bob_bundle["identity_pub_b64"]),
        peer_dh_pub_bytes=base64.b64decode(bob_bundle["dh_pub_b64"]),
        my_user_id=alice_user_id,
        peer_user_id=bob_user_id,
        conversation_id=conv_id,
    )
    alice_send, alice_recv = derive_ratchet_chains(alice_sk.raw, initiator=True)

    # 7. Alice encrypts a message
    plaintext = "Hello Bob, this is E2EE!"
    envelope = build_and_encrypt(
        send_chain=alice_send,
        plaintext=plaintext,
        sender_id=alice_user_id,
        recipient_id=bob_user_id,
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )
    envelope.eph_pub_b64 = base64.b64encode(eph_pub_bytes).decode()
    envelope.conv_dh_pub_b64 = base64.b64encode(conv_dh_pub_bytes).decode()

    # 8. Alice POSTs the message
    resp = await client.post(
        "/v1/messages",
        json={"envelope": envelope.model_dump()},
        headers=_auth(alice_token),
    )
    assert resp.status_code == 201, resp.text
    msg_id = resp.json()["id"]

    # 9. Bob fetches messages
    resp = await client.get(
        f"/v1/messages?conversation_id={conv_id}",
        headers=_auth(bob_token),
    )
    assert resp.status_code == 200, resp.text
    messages = resp.json()["messages"]
    assert len(messages) == 1
    received = messages[0]
    assert received["id"] == msg_id

    # 10. Bob derives session key as responder
    alice_bundle_resp = await client.get("/v1/keys/alice_e2e", headers=_auth(bob_token))
    alice_bundle = alice_bundle_resp.json()

    bob_sk = derive_session_key_as_responder(
        my_identity_kp=bob_id_kp,
        my_dh_kp=bob_dh_kp,
        peer_identity_pub_bytes=base64.b64decode(alice_bundle["identity_pub_b64"]),
        peer_dh_pub_bytes=base64.b64decode(alice_bundle["dh_pub_b64"]),
        eph_pub_bytes=base64.b64decode(received["eph_pub_b64"]),
        conv_dh_pub_bytes=base64.b64decode(received["conv_dh_pub_b64"]),
        my_user_id=bob_user_id,
        peer_user_id=alice_user_id,
        conversation_id=conv_id,
    )
    _, bob_recv = derive_ratchet_chains(bob_sk.raw, initiator=False)

    # Both sides must derive the same key
    assert alice_sk.raw == bob_sk.raw

    # 11. Bob decrypts
    from shared.protocol import MessageEnvelope

    env = MessageEnvelope.model_validate(received)
    rp = ReplayProtector()
    decrypted = decrypt_envelope(recv_chain=bob_recv, envelope=env, replay_protector=rp)
    assert decrypted == plaintext

    # 12. Bob sends ACK
    resp = await client.post(
        "/v1/messages/ack",
        json={"message_id": msg_id, "conversation_id": conv_id},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 204, resp.text


@pytest.mark.anyio
@pytest.mark.asyncio
async def test_replay_rejection(app_client: AsyncClient):
    """
    Posting the same message envelope twice must return 409 on the second attempt.
    """
    client = app_client

    alice_id_kp, alice_dh_kp, alice_totp = await _register(
        client, "alice_replay", "AlicePassword123!"
    )
    bob_id_kp, bob_dh_kp, bob_totp = await _register(client, "bob_replay", "BobPassword123!")

    alice_token = await _login(client, "alice_replay", "AlicePassword123!", alice_totp)
    bob_token = await _login(client, "bob_replay", "BobPassword123!", bob_totp)

    # Friend request
    resp = await client.post(
        "/v1/friends/request",
        json={"recipient_username": "bob_replay"},
        headers=_auth(alice_token),
    )
    request_id = resp.json()["id"]
    await client.put(
        f"/v1/friends/request/{request_id}",
        json={"action": "accept"},
        headers=_auth(bob_token),
    )

    alice_resp = await client.get("/v1/keys/alice_replay", headers=_auth(alice_token))
    bob_resp = await client.get("/v1/keys/bob_replay", headers=_auth(alice_token))
    alice_user_id = alice_resp.json()["user_id"]
    bob_user_id = bob_resp.json()["user_id"]
    bob_bundle = bob_resp.json()

    # Get conversation ID from server
    conv_resp = await client.get("/v1/conversations", headers=_auth(alice_token))
    assert conv_resp.status_code == 200, conv_resp.text
    conv_id = conv_resp.json()["conversations"][0]["id"]

    alice_sk, eph_pub_bytes, conv_dh_pub_bytes = derive_session_key_as_initiator(
        my_identity_kp=alice_id_kp,
        my_dh_kp=alice_dh_kp,
        peer_identity_pub_bytes=base64.b64decode(bob_bundle["identity_pub_b64"]),
        peer_dh_pub_bytes=base64.b64decode(bob_bundle["dh_pub_b64"]),
        my_user_id=alice_user_id,
        peer_user_id=bob_user_id,
        conversation_id=conv_id,
    )
    alice_send, _ = derive_ratchet_chains(alice_sk.raw, initiator=True)

    envelope = build_and_encrypt(
        send_chain=alice_send,
        plaintext="replay test",
        sender_id=alice_user_id,
        recipient_id=bob_user_id,
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )
    envelope.eph_pub_b64 = base64.b64encode(eph_pub_bytes).decode()
    envelope.conv_dh_pub_b64 = base64.b64encode(conv_dh_pub_bytes).decode()

    # First POST — must succeed
    resp1 = await client.post(
        "/v1/messages",
        json={"envelope": envelope.model_dump()},
        headers=_auth(alice_token),
    )
    assert resp1.status_code == 201, resp1.text

    # Second POST — same envelope — must be rejected as replay
    resp2 = await client.post(
        "/v1/messages",
        json={"envelope": envelope.model_dump()},
        headers=_auth(alice_token),
    )
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["detail"] == "Duplicate message (replay rejected)"


async def test_non_replay_db_failure_is_not_misclassified(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    client = app_client
    pw = "Passw0rd!123"

    alice_id_kp, alice_dh_kp, a_sec = await _register(client, "dbfail_alice", pw)
    bob_id_kp, bob_dh_kp, b_sec = await _register(client, "dbfail_bob", pw)
    alice_token = await _login(client, "dbfail_alice", pw, a_sec)
    bob_token = await _login(client, "dbfail_bob", pw, b_sec)

    alice_resp = await client.get("/v1/keys/dbfail_alice", headers=_auth(alice_token))
    bob_resp = await client.get("/v1/keys/dbfail_bob", headers=_auth(alice_token))
    assert alice_resp.status_code == 200, alice_resp.text
    assert bob_resp.status_code == 200, bob_resp.text
    alice_user_id = alice_resp.json()["user_id"]
    bob_user_id = bob_resp.json()["user_id"]
    bob_bundle = bob_resp.json()

    resp = await client.post(
        "/v1/friends/request",
        json={"recipient_username": "dbfail_bob"},
        headers=_auth(alice_token),
    )
    assert resp.status_code == 201, resp.text
    req_id = resp.json()["id"]

    resp = await client.put(
        f"/v1/friends/request/{req_id}",
        json={"action": "accept"},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 204, resp.text

    conv_resp = await client.get("/v1/conversations", headers=_auth(alice_token))
    assert conv_resp.status_code == 200, conv_resp.text
    conv_id = conv_resp.json()["conversations"][0]["id"]

    alice_sk, eph_pub_bytes, conv_dh_pub_bytes = derive_session_key_as_initiator(
        my_identity_kp=alice_id_kp,
        my_dh_kp=alice_dh_kp,
        peer_identity_pub_bytes=base64.b64decode(bob_bundle["identity_pub_b64"]),
        peer_dh_pub_bytes=base64.b64decode(bob_bundle["dh_pub_b64"]),
        my_user_id=alice_user_id,
        peer_user_id=bob_user_id,
        conversation_id=conv_id,
    )
    alice_send, _ = derive_ratchet_chains(alice_sk.raw, initiator=True)
    envelope = build_and_encrypt(
        send_chain=alice_send,
        plaintext="db failure should not look like replay",
        sender_id=alice_user_id,
        recipient_id=bob_user_id,
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )
    envelope.eph_pub_b64 = base64.b64encode(eph_pub_bytes).decode()
    envelope.conv_dh_pub_b64 = base64.b64encode(conv_dh_pub_bytes).decode()

    async def _fail_flush(*_args, **_kwargs):
        raise OperationalError("INSERT INTO messages", {}, RuntimeError("db unavailable"))

    import server.api.messages as messages_api

    monkeypatch.setattr(messages_api.AsyncSession, "flush", _fail_flush)

    with pytest.raises(OperationalError):
        await client.post(
            "/v1/messages",
            json={"envelope": envelope.model_dump()},
            headers=_auth(alice_token),
        )


@pytest.mark.anyio
@pytest.mark.asyncio
async def test_online_push_does_not_mark_delivered_before_ack(
    app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app_client

    alice_id_kp, alice_dh_kp, alice_totp = await _register(
        client, "alice_delivery", "AlicePassword123!"
    )
    bob_id_kp, bob_dh_kp, bob_totp = await _register(client, "bob_delivery", "BobPassword123!")

    alice_token = await _login(client, "alice_delivery", "AlicePassword123!", alice_totp)
    bob_token = await _login(client, "bob_delivery", "BobPassword123!", bob_totp)

    resp = await client.post(
        "/v1/friends/request",
        json={"recipient_username": "bob_delivery"},
        headers=_auth(alice_token),
    )
    assert resp.status_code == 201, resp.text
    request_id = resp.json()["id"]

    resp = await client.put(
        f"/v1/friends/request/{request_id}",
        json={"action": "accept"},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 204, resp.text

    resp = await client.get("/v1/keys/bob_delivery", headers=_auth(alice_token))
    assert resp.status_code == 200, resp.text
    bob_bundle = resp.json()
    bob_user_id = bob_bundle["user_id"]

    resp = await client.get("/v1/keys/alice_delivery", headers=_auth(alice_token))
    assert resp.status_code == 200, resp.text
    alice_user_id = resp.json()["user_id"]

    resp = await client.get("/v1/conversations", headers=_auth(alice_token))
    assert resp.status_code == 200, resp.text
    conv_id = resp.json()["conversations"][0]["id"]

    alice_sk, eph_pub_bytes, conv_dh_pub_bytes = derive_session_key_as_initiator(
        my_identity_kp=alice_id_kp,
        my_dh_kp=alice_dh_kp,
        peer_identity_pub_bytes=base64.b64decode(bob_bundle["identity_pub_b64"]),
        peer_dh_pub_bytes=base64.b64decode(bob_bundle["dh_pub_b64"]),
        my_user_id=alice_user_id,
        peer_user_id=bob_user_id,
        conversation_id=conv_id,
    )
    alice_send, _ = derive_ratchet_chains(alice_sk.raw, initiator=True)
    envelope = build_and_encrypt(
        send_chain=alice_send,
        plaintext="delivery waits for ack",
        sender_id=alice_user_id,
        recipient_id=bob_user_id,
        conversation_id=conv_id,
        counter=0,
        ttl_seconds=None,
        sent_at=int(time.time()),
    )
    envelope.eph_pub_b64 = base64.b64encode(eph_pub_bytes).decode()
    envelope.conv_dh_pub_b64 = base64.b64encode(conv_dh_pub_bytes).decode()

    pushed_payloads: list[dict[str, object]] = []

    async def _push_to_user(_user_id: str, payload: dict[str, object]) -> bool:
        pushed_payloads.append(payload)
        return True

    import server.api.messages as messages_api

    monkeypatch.setattr(messages_api, "push_to_user", _push_to_user)

    resp = await client.post(
        "/v1/messages",
        json={"envelope": envelope.model_dump()},
        headers=_auth(alice_token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["delivered_at"] is None
    assert [payload["type"] for payload in pushed_payloads] == ["message"]

    resp = await client.get(f"/v1/messages?conversation_id={conv_id}", headers=_auth(bob_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["messages"][0]["delivery_status"] == "sent"

    resp = await client.post(
        "/v1/messages/ack",
        json={"message_id": envelope.id, "conversation_id": conv_id},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 204, resp.text
    assert [payload["type"] for payload in pushed_payloads] == ["message", "ack"]

    resp = await client.get(f"/v1/messages?conversation_id={conv_id}", headers=_auth(bob_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["messages"][0]["delivery_status"] == "delivered"
