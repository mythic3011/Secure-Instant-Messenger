"""
server/api/messages.py — Send, receive, delivery ACK, pagination.
Covers: R8, R9, R16, R17, R18, R20, R21, R22
"""

from __future__ import annotations

import base64
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from server.api.auth import require_auth
from server.core.database import get_db
from server.services.message_service import MessageService
from shared.protocol import (
    DeliveryAck,
    FetchMessagesResponse,
    MessageEnvelope,
    SendMessageRequest,
    SendMessageResponse,
)

router = APIRouter(prefix="/v1/messages", tags=["messages"])

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_MAX_CIPHERTEXT_B64_LEN = 88_000  # ~64 KB plaintext + GCM overhead + base64


def _validate_envelope(env: MessageEnvelope) -> None:
    """Input validation for message envelopes (PDF §7)."""
    if not _UUID_RE.match(env.id):
        raise HTTPException(status_code=422, detail="Invalid message id format")
    try:
        nonce = base64.b64decode(env.nonce_b64, validate=True)
        if len(nonce) != 12:
            raise ValueError
    except Exception as err:
        raise HTTPException(
            status_code=422,
            detail="nonce_b64 must decode to exactly 12 bytes",
        ) from err
    if len(env.ciphertext_b64) > _MAX_CIPHERTEXT_B64_LEN:
        raise HTTPException(status_code=422, detail="Message too large")
    if env.eph_pub_b64 is not None:
        try:
            eph = base64.b64decode(env.eph_pub_b64, validate=True)
            if len(eph) != 32:
                raise ValueError
        except Exception as err:
            raise HTTPException(
                status_code=422,
                detail="eph_pub_b64 must decode to exactly 32 bytes",
            ) from err
    if env.conv_dh_pub_b64 is not None:
        try:
            cdh = base64.b64decode(env.conv_dh_pub_b64, validate=True)
            if len(cdh) != 32:
                raise ValueError
        except Exception as err:
            raise HTTPException(
                status_code=422,
                detail="conv_dh_pub_b64 must decode to exactly 32 bytes",
            ) from err
    if env.ttl_seconds is not None and not (1 <= env.ttl_seconds <= 604800):
        raise HTTPException(status_code=422, detail="ttl_seconds must be 1–604800")
    if env.counter < 0:
        raise HTTPException(status_code=422, detail="counter must be non-negative")


# ---------------------------------------------------------------------------
# R8, R16, R20 — Send message
# ---------------------------------------------------------------------------


@router.post(
    "", response_model=SendMessageResponse, status_code=status.HTTP_201_CREATED
)
async def send_message(
    body: SendMessageRequest,
    *,
    session: Annotated[dict, Depends(require_auth)],
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

    db = await get_db()
    return await MessageService(db).store_message(env, session["user_id"])


# ---------------------------------------------------------------------------
# R25 — Fetch messages (paginated, cursor-based)
# ---------------------------------------------------------------------------


@router.get("", response_model=FetchMessagesResponse)
async def fetch_messages(
    conversation_id: str,
    before_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=100),
    *,
    session: Annotated[dict, Depends(require_auth)],
) -> FetchMessagesResponse:
    """
    Fetch messages for a conversation (cursor-based pagination).
    Only returns messages where the caller is sender or recipient.
    """
    db = await get_db()
    return await MessageService(db).fetch_messages(
        conversation_id=conversation_id,
        user_id=session["user_id"],
        before_id=before_id,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# R18 — Delivery ACK (Option B: recipient sends ACK after decryption)
# ---------------------------------------------------------------------------


@router.post("/ack", status_code=status.HTTP_204_NO_CONTENT)
async def delivery_ack(
    body: DeliveryAck,
    *,
    session: Annotated[dict, Depends(require_auth)],
) -> None:
    """
    Recipient sends this after successfully decrypting a message.
    Updates delivered_at on the message and notifies the sender.
    """
    db = await get_db()
    await MessageService(db).process_delivery_ack(body, session["user_id"])
