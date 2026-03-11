"""
server/api/conversations.py — Conversation list, unread counters.
Covers: R23, R24
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends

from server.api.auth import require_auth
from server.core.database import get_db
from shared.protocol import ConversationListResponse, ConversationOut

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
log = structlog.get_logger()


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    session: dict = Depends(require_auth),
) -> ConversationListResponse:
    """
    Return all conversations for the current user, ordered by most recent activity.
    Includes unread count per conversation (R24).
    """
    db = await get_db()
    user_id = session["user_id"]

    async with db.execute(
        """SELECT
               c.id,
               CASE WHEN c.user_a_id = ? THEN c.user_b_id ELSE c.user_a_id END AS peer_id,
               CASE WHEN c.user_a_id = ? THEN ub.username  ELSE ua.username  END AS peer_username,
               c.last_message_at,
               CASE WHEN c.user_a_id = ? THEN c.unread_count_a ELSE c.unread_count_b END AS unread_count
           FROM conversations c
           JOIN users ua ON ua.id = c.user_a_id
           JOIN users ub ON ub.id = c.user_b_id
           WHERE (c.user_a_id = ? OR c.user_b_id = ?)
             AND ua.deleted_at IS NULL AND ub.deleted_at IS NULL
           ORDER BY c.last_message_at DESC NULLS LAST""",
        (user_id, user_id, user_id, user_id, user_id),
    ) as cur:
        rows = await cur.fetchall()

    return ConversationListResponse(
        conversations=[
            ConversationOut(
                id=r["id"],
                peer_id=r["peer_id"],
                peer_username=r["peer_username"],
                last_message_at=r["last_message_at"],
                unread_count=r["unread_count"],
            )
            for r in rows
        ]
    )


@router.post("/{conversation_id}/read", status_code=204)
async def mark_read(
    conversation_id: str,
    session: dict = Depends(require_auth),
) -> None:
    """Reset the unread counter for the current user in a conversation."""
    db = await get_db()
    user_id = session["user_id"]

    async with db.execute(
        "SELECT user_a_id, user_b_id FROM conversations WHERE id = ?",
        (conversation_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return

    col = "unread_count_a" if row["user_a_id"] == user_id else "unread_count_b"
    await db.execute(
        f"UPDATE conversations SET {col} = 0 WHERE id = ?", (conversation_id,)
    )
    await db.commit()
