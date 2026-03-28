"""
server/api/messages.py — Send, receive, delivery ACK, pagination.
Covers: R8, R9, R16, R17, R18, R20, R21, R22
"""

from __future__ import annotations

import base64
import re
import time
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.auth import require_auth
from server.core.database import get_db
from server.models import Conversation, Friendship, Message, User
from server.services.delivery import mark_delivered_on_ack
from server.ws.events import build_message_event
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
_MAX_CIPHERTEXT_B64_LEN = 88_000  # ~64 KB plaintext + GCM overhead + base64
_REPLAY_CONSTRAINT_NAME = "uq_message_replay"
_REPLAY_UNIQUE_MARKERS = (
    "messages.conversation_id",
    "messages.sender_id",
    "messages.counter",
)


def _validate_envelope(env: MessageEnvelope) -> None:
    """Input validation for message envelopes (PDF §7)."""
    if not _UUID_RE.match(env.id):
        raise HTTPException(status_code=422, detail="Invalid message id format")
    try:
        nonce = base64.b64decode(env.nonce_b64, validate=True)
        if len(nonce) != 12:
            raise ValueError
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="nonce_b64 must decode to exactly 12 bytes",
        ) from exc
    if len(env.ciphertext_b64) > _MAX_CIPHERTEXT_B64_LEN:
        raise HTTPException(status_code=422, detail="Message too large")
    if env.eph_pub_b64 is not None:
        try:
            eph = base64.b64decode(env.eph_pub_b64, validate=True)
            if len(eph) != 32:
                raise ValueError
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="eph_pub_b64 must decode to exactly 32 bytes",
            ) from exc
    if env.conv_dh_pub_b64 is not None:
        try:
            cdh = base64.b64decode(env.conv_dh_pub_b64, validate=True)
            if len(cdh) != 32:
                raise ValueError
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="conv_dh_pub_b64 must decode to exactly 32 bytes",
            ) from exc
    if env.ttl_seconds is not None and not (1 <= env.ttl_seconds <= 604800):
        raise HTTPException(status_code=422, detail="ttl_seconds must be 1–604800")
    if env.counter < 0:
        raise HTTPException(status_code=422, detail="counter must be non-negative")


def _is_replay_unique_violation(exc: IntegrityError) -> bool:
    """Return True only for the replay-prevention unique constraint."""
    orig = getattr(exc, "orig", None)
    constraint_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint_name == _REPLAY_CONSTRAINT_NAME:
        return True

    text = " ".join(str(part).lower() for part in (exc, orig) if part is not None)
    if _REPLAY_CONSTRAINT_NAME in text:
        return True

    return "unique constraint failed" in text and all(
        marker in text for marker in _REPLAY_UNIQUE_MARKERS
    )


# ---------------------------------------------------------------------------
# R8, R16, R20 — Send message
# ---------------------------------------------------------------------------


@router.post("", response_model=SendMessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    body: SendMessageRequest,
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
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
        log.warning(
            "send_message_rejected",
            reason="sender_id_mismatch",
            claimed_sender=env.sender_id,
            actual_user=session["user_id"],
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="sender_id mismatch")

    # R16 — verify friendship before accepting message
    # Race condition fix: Use SELECT...FOR UPDATE to lock the friendship row
    # and prevent concurrent modifications (e.g., unfriending) during message insertion
    a, b = sorted([env.sender_id, env.recipient_id])
    stmt = (
        select(Friendship)
        .where(
            Friendship.user_a_id == a,
            Friendship.user_b_id == b,
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        log.warning(
            "send_message_rejected",
            reason="not_friends",
            sender=env.sender_id,
            recipient=env.recipient_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not friends")

    # Verify recipient exists
    stmt = select(User).where(User.id == env.recipient_id, User.deleted_at.is_(None))
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        log.warning(
            "send_message_rejected",
            reason="recipient_not_found",
            sender=env.sender_id,
            recipient=env.recipient_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")

    # Ensure conversation row exists — look up by participants
    a, b = sorted([env.sender_id, env.recipient_id])
    stmt = select(Conversation).where(
        Conversation.user_a_id == a,
        Conversation.user_b_id == b,
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No conversation exists (add friend first)",
        )
    conv_id = conv.id
    if conv_id != env.conversation_id:
        raise HTTPException(status_code=422, detail="conversation_id mismatch")

    # Server-side replay prevention: the UNIQUE(conversation_id, sender_id, counter)
    # constraint rejects duplicate counters at the DB level. This is the second
    # layer of replay defense — the client also checks counters locally. Having
    # both layers means a compromised client cannot replay messages to other
    # clients through the server, and a compromised server cannot replay
    # messages to clients (client-side check catches it).
    #
    # Metadata exposure: The server stores and can see sender_id, recipient_id,
    # conversation_id, counter, ttl_seconds, sent_at, and delivery_status.
    # This is inherent to server-assisted messaging — the server needs this
    # metadata to route messages and track delivery. Plaintext content remains
    # encrypted and invisible to the server.
    sent_at_dt = datetime.fromtimestamp(env.sent_at)
    message = Message(
        id=env.id,
        conversation_id=conv_id,
        sender_id=env.sender_id,
        recipient_id=env.recipient_id,
        counter=env.counter,
        nonce_b64=env.nonce_b64,
        ciphertext_b64=env.ciphertext_b64,
        eph_pub_b64=env.eph_pub_b64,
        conv_dh_pub_b64=env.conv_dh_pub_b64,
        chain_index=env.chain_index,
        ttl_seconds=env.ttl_seconds,
        sent_at=sent_at_dt,
    )
    db.add(message)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        if _is_replay_unique_violation(exc):
            log.warning(
                "replay_rejected",
                msg_id=env.id,
                sender=env.sender_id,
                counter=env.counter,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate message (replay rejected)",
            ) from exc
        raise

    # Update conversation metadata
    is_a = env.recipient_id == a
    if is_a:
        conv.unread_count_a += 1
    else:
        conv.unread_count_b += 1
    conv.last_message_at = sent_at_dt
    await db.commit()

    stored_at = int(time.time())

    # Push to recipient if online (WebSocket)
    delivered_at = None
    pushed = await push_to_user(env.recipient_id, build_message_event(env))

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
    db: AsyncSession = Depends(get_db),
) -> FetchMessagesResponse:
    """
    Fetch messages for a conversation (cursor-based pagination).
    Only returns messages where the caller is sender or recipient.
    """
    user_id = session["user_id"]

    # Verify caller is part of this conversation
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        (Conversation.user_a_id == user_id) | (Conversation.user_b_id == user_id),
    )
    result = await db.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not part of this conversation"
        )

    if before_id:
        stmt = select(Message.sent_at).where(
            Message.id == before_id,
            Message.conversation_id == conversation_id,
        )
        result = await db.execute(stmt)
        cursor_row = result.scalar_one_or_none()
        if cursor_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Cursor message not found"
            )
        cursor_ts = cursor_row

        stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.sent_at < cursor_ts,
            )
            .order_by(Message.sent_at.desc())
            .limit(limit + 1)
        )
    else:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sent_at.desc())
            .limit(limit + 1)
        )

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    has_more = len(rows) > limit
    rows = rows[:limit]

    # Metadata exposure: When fetching messages, the server reveals metadata
    # including sender_id, recipient_id, conversation_id, counter, sent_at,
    # delivered_at, and delivery_status. This allows clients to display
    # conversation history and delivery indicators, but plaintext remains
    # encrypted. The server cannot read message content.
    messages = [
        MessageEnvelope(
            id=msg.id,
            type=MessageType.MESSAGE,
            sender_id=msg.sender_id,
            recipient_id=msg.recipient_id,
            conversation_id=conversation_id,
            counter=msg.counter,
            nonce_b64=msg.nonce_b64,
            ciphertext_b64=msg.ciphertext_b64,
            eph_pub_b64=msg.eph_pub_b64,
            conv_dh_pub_b64=msg.conv_dh_pub_b64,
            chain_index=msg.chain_index if msg.chain_index is not None else 0,
            ttl_seconds=msg.ttl_seconds,
            sent_at=int(msg.sent_at.timestamp())
            if isinstance(msg.sent_at, datetime)
            else msg.sent_at,
            delivery_status=DeliveryStatus.DELIVERED if msg.delivered_at else DeliveryStatus.SENT,
        )
        for msg in rows
    ]

    result = FetchMessagesResponse(
        messages=messages,
        has_more=has_more,
        next_cursor=rows[-1].id if has_more and rows else None,
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
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Recipient sends this after successfully decrypting a message.
    Updates delivered_at on the message and notifies the sender.
    """
    user_id = session["user_id"]

    ack = await mark_delivered_on_ack(
        db,
        message_id=body.message_id,
        actor_id=user_id,
    )

    if ack.status == "not_found":
        log.debug("delivery_ack_ignored", message_id=body.message_id, reason="not_found")
        return  # silently ignore unknown message IDs

    if ack.status == "not_recipient":
        log.warning(
            "delivery_ack_rejected",
            message_id=body.message_id,
            user_id=user_id,
            reason="not_recipient",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your message")

    if ack.status == "delivered" and ack.sender_id and ack.delivered_at_ts is not None:
        await push_to_user(
            ack.sender_id,
            {
                "type": "ack",
                "payload": {"message_id": body.message_id, "delivered_at": ack.delivered_at_ts},
            },
        )
        log.info(
            "delivery_ack_processed",
            message_id=body.message_id,
            recipient=user_id,
            sender=ack.sender_id,
        )
