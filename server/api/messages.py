"""
server/api/messages.py — Send, receive, delivery ACK, pagination.
Covers: R8, R9, R16, R17, R18, R20, R21, R22
"""

from __future__ import annotations

import base64
import re
import time

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from server.api.auth import require_auth
from server.core.database import get_db
from server.ws.handler import push_to_user
from shared.protocol import (
    DeliveryAck,
    DeliveryStatus,
    FetchMessagesResponse,
    MessageEnvelope,
    MessageType,
    SendMessageRequest,
    SendMessageResponse,
)

router = APIRouter(prefix="/v1/messages", tags=["messages"])
log = structlog.get_logger()

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_MAX_CIPHERTEXT_B64_LEN = 88_000   # ~64 KB plaintext + GCM overhead + base64


def _validate_envelope(env: MessageEnvelope) -> None:
    """Input validation for message envelopes (PDF §7)."""
    if not _UUID_RE.match(env.id):
        raise HTTPException(status_code=422, detail="Invalid message id format")
    try:
        nonce = base64.b64decode(env.nonce_b64, validate=True)
        if len(nonce) != 12:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=422, detail="nonce_b64 must decode to exactly 12 bytes")
    if len(env.ciphertext_b64) > _MAX_CIPHERTEXT_B64_LEN:
        raise HTTPException(status_code=422, detail="Message too large")
    if env.eph_pub_b64 is not None:
        try:
            eph = base64.b64decode(env.eph_pub_b64, validate=True)
            if len(eph) != 32:
                raise ValueError
        except Exception:
            raise HTTPException(status_code=422, detail="eph_pub_b64 must decode to exactly 32 bytes")
    if env.conv_dh_pub_b64 is not None:
        try:
            cdh = base64.b64decode(env.conv_dh_pub_b64, validate=True)
            if len(cdh) != 32:
                raise ValueError
        except Exception:
            raise HTTPException(status_code=422, detail="conv_dh_pub_b64 must decode to exactly 32 bytes")
    if env.ttl_seconds is not None and not (1 <= env.ttl_seconds <= 604800):
        raise HTTPException(status_code=422, detail="ttl_seconds must be 1–604800")
    if env.counter < 0:
        raise HTTPException(status_code=422, detail="counter must be non-negative")


# ---------------------------------------------------------------------------
# R8, R16, R20 — Send message
# ---------------------------------------------------------------------------

@router.post("", response_model=SendMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    body: SendMessageRequest,
    session: dict = Depends(require_auth),
) -> SendMessageResponse:
    """
    Accept an encrypted message envelope from the sender.
    - Validates sender identity
    - Enforces friendship (R16 anti-spam)
    - Server-side replay prevention via UNIQUE(conversation_id, sender_id, counter)
    - Pushes to recipient via WebSocket if online, else queues for offline delivery
    """
    env = body.envelope
    _validate_envelope(env)

    if env.sender_id != session["user_id"]:
        log.warning("send_message_rejected", reason="sender_id_mismatch", claimed_sender=env.sender_id, actual_user=session["user_id"])
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="sender_id mismatch")

    db = await get_db()

    # R16 — verify friendship before accepting message
    a, b = sorted([env.sender_id, env.recipient_id])
    async with db.execute(
        "SELECT 1 FROM friendships WHERE user_a_id = ? AND user_b_id = ?", (a, b)
    ) as cur:
        if not await cur.fetchone():
            log.warning("send_message_rejected", reason="not_friends", sender=env.sender_id, recipient=env.recipient_id)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not friends")

    # Verify recipient exists
    async with db.execute(
        "SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (env.recipient_id,)
    ) as cur:
        if not await cur.fetchone():
            log.warning("send_message_rejected", reason="recipient_not_found", sender=env.sender_id, recipient=env.recipient_id)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")

    # Ensure conversation row exists — look up by participants
    a, b = sorted([env.sender_id, env.recipient_id])
    async with db.execute(
        "SELECT id FROM conversations WHERE user_a_id = ? AND user_b_id = ?", (a, b)
    ) as cur:
        conv_row = await cur.fetchone()
    if conv_row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No conversation exists (add friend first)")
    conv_id = conv_row["id"]
    if conv_id != env.conversation_id:
        raise HTTPException(status_code=422, detail="conversation_id mismatch")

    # Server-side replay prevention: the UNIQUE(conversation_id, sender_id, counter)
    # constraint rejects duplicate counters at the DB level. This is the second
    # layer of replay defense — the client also checks counters locally. Having
    # both layers means a compromised client cannot replay messages to other
    # clients through the server, and a compromised server cannot replay
    # messages to clients (client-side check catches it).
    try:
        await db.execute(
            """INSERT INTO messages
               (id, conversation_id, sender_id, recipient_id, counter,
                nonce_b64, ciphertext_b64, eph_pub_b64, conv_dh_pub_b64, chain_index,
                ttl_seconds, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                env.id, conv_id, env.sender_id, env.recipient_id, env.counter,
                env.nonce_b64, env.ciphertext_b64, env.eph_pub_b64,
                env.conv_dh_pub_b64, env.chain_index,
                env.ttl_seconds, env.sent_at,
            ),
        )
    except Exception:
        # UNIQUE constraint violation = replay attempt
        log.warning("replay_rejected", msg_id=env.id, sender=env.sender_id, counter=env.counter)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicate message (replay rejected)")

    # Update conversation metadata
    is_a = env.recipient_id == a
    unread_col = "unread_count_a" if is_a else "unread_count_b"
    await db.execute(
        f"UPDATE conversations SET last_message_at = ?, {unread_col} = {unread_col} + 1 WHERE id = ?",
        (env.sent_at, conv_id),
    )
    await db.commit()

    stored_at = int(time.time())

    # Push to recipient if online (WebSocket)
    delivered_at = None
    pushed = await push_to_user(env.recipient_id, {"type": "message", "payload": env.model_dump()})
    if pushed:
        delivered_at = stored_at
        await db.execute(
            "UPDATE messages SET delivered_at = ? WHERE id = ?", (delivered_at, env.id)
        )
        await db.commit()

    log.info(
        "message_stored",
        msg_id=env.id,
        sender=env.sender_id,
        recipient=env.recipient_id,
        pushed=pushed,
        ttl_seconds=env.ttl_seconds,
    )
    return SendMessageResponse(id=env.id, stored_at=stored_at, delivered_at=delivered_at)


# ---------------------------------------------------------------------------
# R25 — Fetch messages (paginated, cursor-based)
# ---------------------------------------------------------------------------

@router.get("", response_model=FetchMessagesResponse)
async def fetch_messages(
    conversation_id: str,
    before_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=100),
    session: dict = Depends(require_auth),
) -> FetchMessagesResponse:
    """
    Fetch messages for a conversation (cursor-based pagination).
    Only returns messages where the caller is sender or recipient.
    """
    db = await get_db()
    user_id = session["user_id"]

    # Verify caller is part of this conversation
    async with db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND (user_a_id = ? OR user_b_id = ?)",
        (conversation_id, user_id, user_id),
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not part of this conversation")

    if before_id:
        async with db.execute(
            "SELECT sent_at FROM messages WHERE id = ?", (before_id,)
        ) as cur:
            cursor_row = await cur.fetchone()
        if cursor_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cursor message not found")
        cursor_ts = cursor_row["sent_at"]

        async with db.execute(
            """SELECT id, sender_id, recipient_id, counter, nonce_b64, ciphertext_b64,
                      eph_pub_b64, conv_dh_pub_b64, chain_index, ttl_seconds, sent_at, delivered_at
               FROM messages
               WHERE conversation_id = ? AND sent_at < ?
               ORDER BY sent_at DESC LIMIT ?""",
            (conversation_id, cursor_ts, limit + 1),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            """SELECT id, sender_id, recipient_id, counter, nonce_b64, ciphertext_b64,
                      eph_pub_b64, conv_dh_pub_b64, chain_index, ttl_seconds, sent_at, delivered_at
               FROM messages
               WHERE conversation_id = ?
               ORDER BY sent_at DESC LIMIT ?""",
            (conversation_id, limit + 1),
        ) as cur:
            rows = await cur.fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]

    messages = [
        MessageEnvelope(
            id=r["id"],
            type=MessageType.MESSAGE,
            sender_id=r["sender_id"],
            recipient_id=r["recipient_id"],
            conversation_id=conversation_id,
            counter=r["counter"],
            nonce_b64=r["nonce_b64"],
            ciphertext_b64=r["ciphertext_b64"],
            eph_pub_b64=r["eph_pub_b64"],
            conv_dh_pub_b64=r["conv_dh_pub_b64"],
            chain_index=r["chain_index"] if r["chain_index"] is not None else 0,
            ttl_seconds=r["ttl_seconds"],
            sent_at=r["sent_at"],
            delivery_status=DeliveryStatus.DELIVERED if r["delivered_at"] else DeliveryStatus.SENT,
        )
        for r in rows
    ]

    result = FetchMessagesResponse(
        messages=messages,
        has_more=has_more,
        next_cursor=rows[-1]["id"] if has_more and rows else None,
    )
    log.debug(
        "fetch_messages",
        user_id=user_id,
        conversation_id=conversation_id,
        count=len(messages),
        has_more=has_more,
    )
    return result


# ---------------------------------------------------------------------------
# R18 — Delivery ACK (Option B: recipient sends ACK after decryption)
# ---------------------------------------------------------------------------

@router.post("/ack", status_code=status.HTTP_204_NO_CONTENT)
async def delivery_ack(
    body: DeliveryAck,
    session: dict = Depends(require_auth),
) -> None:
    """
    Recipient sends this after successfully decrypting a message.
    Updates delivered_at on the message and notifies the sender.
    """
    db = await get_db()
    user_id = session["user_id"]

    async with db.execute(
        "SELECT sender_id, recipient_id, delivered_at FROM messages WHERE id = ?",
        (body.message_id,),
    ) as cur:
        msg = await cur.fetchone()

    if msg is None:
        log.debug("delivery_ack_ignored", message_id=body.message_id, reason="not_found")
        return  # silently ignore unknown message IDs

    if msg["recipient_id"] != user_id:
        log.warning("delivery_ack_rejected", message_id=body.message_id, user_id=user_id, reason="not_recipient")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your message")

    if msg["delivered_at"] is None:
        now = int(time.time())
        await db.execute(
            "UPDATE messages SET delivered_at = ? WHERE id = ?", (now, body.message_id)
        )
        await db.commit()

        # Notify sender of delivery
        await push_to_user(
            msg["sender_id"],
            {"type": "ack", "payload": {"message_id": body.message_id, "delivered_at": now}},
        )
        log.info("delivery_ack_processed", message_id=body.message_id, recipient=user_id, sender=msg["sender_id"])
