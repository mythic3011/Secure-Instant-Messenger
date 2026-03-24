"""
server/api/conversations.py — Conversation list, unread counters.
Covers: R23, R24
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from server.api.auth import require_auth
from server.core.database import get_db
from server.models import Conversation, User
from shared.protocol import ConversationListResponse, ConversationOut

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
log = structlog.get_logger()


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ConversationListResponse:
    """
    Return all conversations for the current user, ordered by most recent activity.
    Includes unread count per conversation (R24).
    """
    user_id = session["user_id"]

    # Create aliases for the two user joins
    user_a = aliased(User)
    user_b = aliased(User)

    # Query conversations with user joins using ORM
    stmt = (
        select(
            Conversation,
            case(
                (Conversation.user_a_id == user_id, user_b.username),
                else_=user_a.username,
            ).label("peer_username"),
            case(
                (Conversation.user_a_id == user_id, Conversation.user_b_id),
                else_=Conversation.user_a_id,
            ).label("peer_id"),
            case(
                (Conversation.user_a_id == user_id, Conversation.unread_count_a),
                else_=Conversation.unread_count_b,
            ).label("unread_count"),
        )
        .join(user_a, user_a.id == Conversation.user_a_id)
        .join(user_b, user_b.id == Conversation.user_b_id)
        .where(
            or_(Conversation.user_a_id == user_id, Conversation.user_b_id == user_id),
            user_a.deleted_at.is_(None),
            user_b.deleted_at.is_(None),
        )
        .order_by(Conversation.last_message_at.desc().nulls_last())
    )
    result = await db.execute(stmt)
    rows = result.all()

    log.debug("list_conversations", user_id=user_id, count=len(rows))
    return ConversationListResponse(
        conversations=[
            ConversationOut(
                id=conv.id,
                peer_id=peer_id,
                peer_username=peer_username,
                last_message_at=conv.last_message_at,
                unread_count=unread_count,
            )
            for conv, peer_username, peer_id, unread_count in rows
        ]
    )


@router.post("/{conversation_id}/read", status_code=204)
async def mark_read(
    conversation_id: str,
    session: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Reset the unread counter for the current user in a conversation."""
    user_id = session["user_id"]

    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        (Conversation.user_a_id == user_id) | (Conversation.user_b_id == user_id),
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()

    if conv is None:
        log.debug("mark_read_skipped", conversation_id=conversation_id, user_id=user_id, reason="not_found_or_not_participant")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not part of this conversation")

    if conv.user_a_id == user_id:
        conv.unread_count_a = 0
    else:
        conv.unread_count_b = 0

    await db.commit()
    log.info("mark_read", conversation_id=conversation_id, user_id=user_id)
