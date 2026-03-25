"""
server/api/conversations.py — Conversation list, unread counters.
Covers: R23, R24
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from server.api.auth import require_auth
from server.core.database import get_db
from server.services.conversation_service import ConversationService
from shared.protocol import ConversationListResponse

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
SessionDep = Depends(require_auth)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    session: dict = SessionDep,
) -> ConversationListResponse:
    """
    Return all conversations for the current user, ordered by most recent activity.
    Includes unread count per conversation (R24).
    """
    db = await get_db()
    return await ConversationService(db).list_for_user(session["user_id"])


@router.post("/{conversation_id}/read", status_code=204)
async def mark_read(
    conversation_id: str,
    session: dict = SessionDep,
) -> None:
    """Reset the unread counter for the current user in a conversation."""
    db = await get_db()
    await ConversationService(db).mark_read(conversation_id, session["user_id"])
