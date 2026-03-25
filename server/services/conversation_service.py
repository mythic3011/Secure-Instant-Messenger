from __future__ import annotations

import aiosqlite
import structlog

from shared.protocol import ConversationListResponse, ConversationOut

log = structlog.get_logger()


class ConversationService:
    """Conversation metadata and unread-counter workflows."""

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def list_for_user(self, user_id: str) -> ConversationListResponse:
        async with self.db.execute(
            """SELECT
                   c.id,
                   CASE
                       WHEN c.user_a_id = ? THEN c.user_b_id
                       ELSE c.user_a_id
                   END AS peer_id,
                   CASE
                       WHEN c.user_a_id = ? THEN ub.username
                       ELSE ua.username
                   END AS peer_username,
                   c.last_message_at,
                   CASE
                       WHEN c.user_a_id = ? THEN c.unread_count_a
                       ELSE c.unread_count_b
                   END AS unread_count
               FROM conversations c
               JOIN users ua ON ua.id = c.user_a_id
               JOIN users ub ON ub.id = c.user_b_id
               WHERE (c.user_a_id = ? OR c.user_b_id = ?)
                 AND ua.deleted_at IS NULL AND ub.deleted_at IS NULL
               ORDER BY c.last_message_at DESC NULLS LAST""",
            (user_id, user_id, user_id, user_id, user_id),
        ) as cur:
            rows = list(await cur.fetchall())

        log.debug("list_conversations", user_id=user_id, count=len(rows))
        return ConversationListResponse(
            conversations=[
                ConversationOut(
                    id=row["id"],
                    peer_id=row["peer_id"],
                    peer_username=row["peer_username"],
                    last_message_at=row["last_message_at"],
                    unread_count=row["unread_count"],
                )
                for row in rows
            ]
        )

    async def mark_read(self, conversation_id: str, user_id: str) -> None:
        async with self.db.execute(
            "SELECT user_a_id, user_b_id FROM conversations WHERE id = ?",
            (conversation_id,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            log.debug(
                "mark_read_skipped",
                conversation_id=conversation_id,
                user_id=user_id,
                reason="not_found",
            )
            return

        if row["user_a_id"] == user_id:
            await self.db.execute(
                "UPDATE conversations SET unread_count_a = 0 WHERE id = ?",
                (conversation_id,),
            )
        else:
            await self.db.execute(
                "UPDATE conversations SET unread_count_b = 0 WHERE id = ?",
                (conversation_id,),
            )
        await self.db.commit()
        log.info("mark_read", conversation_id=conversation_id, user_id=user_id)
