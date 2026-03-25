from __future__ import annotations

import time

import aiosqlite
import structlog
from fastapi import HTTPException, status

from server.ws.handler import push_to_user
from shared.protocol import (
    DeliveryAck,
    DeliveryStatus,
    FetchMessagesResponse,
    MessageEnvelope,
    MessageType,
    SendMessageResponse,
)

log = structlog.get_logger()


class MessageService:
    """Encapsulates message state transitions for the HbC relay."""

    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def store_message(
        self, envelope: MessageEnvelope, authenticated_user_id: str
    ) -> SendMessageResponse:
        if envelope.sender_id != authenticated_user_id:
            log.warning(
                "send_message_rejected",
                reason="sender_id_mismatch",
                claimed_sender=envelope.sender_id,
                actual_user=authenticated_user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="sender_id mismatch",
            )

        await self._ensure_friendship(envelope.sender_id, envelope.recipient_id)
        await self._ensure_recipient_exists(envelope.recipient_id)
        conversation_id = await self._resolve_conversation_id(envelope)
        await self._ensure_monotonic_counter(conversation_id, envelope)
        await self._insert_message(conversation_id, envelope)
        await self._update_conversation_metadata(conversation_id, envelope)

        stored_at = int(time.time())
        delivered_at = await self._push_if_online(envelope)

        log.info(
            "message_stored",
            msg_id=envelope.id,
            sender=envelope.sender_id,
            recipient=envelope.recipient_id,
            pushed=delivered_at is not None,
            ttl_seconds=envelope.ttl_seconds,
        )
        return SendMessageResponse(
            id=envelope.id,
            stored_at=stored_at,
            delivered_at=delivered_at,
        )

    async def fetch_messages(
        self,
        *,
        conversation_id: str,
        user_id: str,
        before_id: str | None,
        limit: int,
    ) -> FetchMessagesResponse:
        async with self.db.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND (user_a_id = ? OR user_b_id = ?)",
            (conversation_id, user_id, user_id),
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not part of this conversation",
                )

        if before_id:
            async with self.db.execute(
                "SELECT sent_at FROM messages WHERE id = ?", (before_id,)
            ) as cur:
                cursor_row = await cur.fetchone()
            if cursor_row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Cursor message not found",
                )
            cursor_ts = cursor_row["sent_at"]

            async with self.db.execute(
                """SELECT
                          id, sender_id, recipient_id, counter, nonce_b64,
                          ciphertext_b64, eph_pub_b64, conv_dh_pub_b64,
                          chain_index, ttl_seconds, sent_at, delivered_at
                   FROM messages
                   WHERE conversation_id = ? AND sent_at < ?
                   ORDER BY sent_at DESC LIMIT ?""",
                (conversation_id, cursor_ts, limit + 1),
            ) as cur:
                rows = list(await cur.fetchall())
        else:
            async with self.db.execute(
                """SELECT
                          id, sender_id, recipient_id, counter, nonce_b64,
                          ciphertext_b64, eph_pub_b64, conv_dh_pub_b64,
                          chain_index, ttl_seconds, sent_at, delivered_at
                   FROM messages
                   WHERE conversation_id = ?
                   ORDER BY sent_at DESC LIMIT ?""",
                (conversation_id, limit + 1),
            ) as cur:
                rows = list(await cur.fetchall())

        has_more = len(rows) > limit
        rows = rows[:limit]

        messages = [
            MessageEnvelope(
                id=row["id"],
                type=MessageType.MESSAGE,
                sender_id=row["sender_id"],
                recipient_id=row["recipient_id"],
                conversation_id=conversation_id,
                counter=row["counter"],
                nonce_b64=row["nonce_b64"],
                ciphertext_b64=row["ciphertext_b64"],
                eph_pub_b64=row["eph_pub_b64"],
                conv_dh_pub_b64=row["conv_dh_pub_b64"],
                chain_index=row["chain_index"] if row["chain_index"] is not None else 0,
                ttl_seconds=row["ttl_seconds"],
                sent_at=row["sent_at"],
                delivery_status=(
                    DeliveryStatus.DELIVERED
                    if row["delivered_at"]
                    else DeliveryStatus.SENT
                ),
            )
            for row in rows
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

    async def process_delivery_ack(
        self, body: DeliveryAck, authenticated_user_id: str
    ) -> None:
        async with self.db.execute(
            "SELECT sender_id, recipient_id, delivered_at FROM messages WHERE id = ?",
            (body.message_id,),
        ) as cur:
            message = await cur.fetchone()

        if message is None:
            log.debug(
                "delivery_ack_ignored", message_id=body.message_id, reason="not_found"
            )
            return

        if message["recipient_id"] != authenticated_user_id:
            log.warning(
                "delivery_ack_rejected",
                message_id=body.message_id,
                user_id=authenticated_user_id,
                reason="not_recipient",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not your message",
            )

        if message["delivered_at"] is not None:
            return

        now = int(time.time())
        await self.db.execute(
            "UPDATE messages SET delivered_at = ? WHERE id = ?", (now, body.message_id)
        )
        await self.db.commit()

        await push_to_user(
            message["sender_id"],
            {
                "type": "ack",
                "payload": {"message_id": body.message_id, "delivered_at": now},
            },
        )
        log.info(
            "delivery_ack_processed",
            message_id=body.message_id,
            recipient=authenticated_user_id,
            sender=message["sender_id"],
        )

    async def _ensure_friendship(self, sender_id: str, recipient_id: str) -> None:
        user_a_id, user_b_id = sorted([sender_id, recipient_id])
        async with self.db.execute(
            "SELECT 1 FROM friendships WHERE user_a_id = ? AND user_b_id = ?",
            (user_a_id, user_b_id),
        ) as cur:
            if await cur.fetchone():
                return

        log.warning(
            "send_message_rejected",
            reason="not_friends",
            sender=sender_id,
            recipient=recipient_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not friends",
        )

    async def _ensure_recipient_exists(self, recipient_id: str) -> None:
        async with self.db.execute(
            "SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (recipient_id,)
        ) as cur:
            if await cur.fetchone():
                return

        log.warning(
            "send_message_rejected",
            reason="recipient_not_found",
            recipient=recipient_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipient not found",
        )

    async def _resolve_conversation_id(self, envelope: MessageEnvelope) -> str:
        user_a_id, user_b_id = sorted([envelope.sender_id, envelope.recipient_id])
        async with self.db.execute(
            "SELECT id FROM conversations WHERE user_a_id = ? AND user_b_id = ?",
            (user_a_id, user_b_id),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No conversation exists (add friend first)",
            )
        conversation_id = row["id"]
        if conversation_id != envelope.conversation_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="conversation_id mismatch",
            )
        return conversation_id

    async def _ensure_monotonic_counter(
        self, conversation_id: str, envelope: MessageEnvelope
    ) -> None:
        async with self.db.execute(
            "SELECT MAX(counter) AS last_counter "
            "FROM messages WHERE conversation_id = ? AND sender_id = ?",
            (conversation_id, envelope.sender_id),
        ) as cur:
            row = await cur.fetchone()

        last_counter = row["last_counter"] if row is not None else None
        if last_counter is not None and envelope.counter <= last_counter:
            log.warning(
                "replay_rejected",
                msg_id=envelope.id,
                sender=envelope.sender_id,
                counter=envelope.counter,
                last_counter=last_counter,
                reason="counter_regression",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Counter replay rejected",
            )

    async def _insert_message(
        self, conversation_id: str, envelope: MessageEnvelope
    ) -> None:
        try:
            await self.db.execute(
                """INSERT INTO messages
                   (id, conversation_id, sender_id, recipient_id, counter,
                    nonce_b64, ciphertext_b64, eph_pub_b64, conv_dh_pub_b64, chain_index,
                    ttl_seconds, sent_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    envelope.id,
                    conversation_id,
                    envelope.sender_id,
                    envelope.recipient_id,
                    envelope.counter,
                    envelope.nonce_b64,
                    envelope.ciphertext_b64,
                    envelope.eph_pub_b64,
                    envelope.conv_dh_pub_b64,
                    envelope.chain_index,
                    envelope.ttl_seconds,
                    envelope.sent_at,
                ),
            )
        except aiosqlite.IntegrityError as exc:
            log.warning(
                "replay_rejected",
                msg_id=envelope.id,
                sender=envelope.sender_id,
                counter=envelope.counter,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate message (replay rejected)",
            ) from exc

    async def _update_conversation_metadata(
        self, conversation_id: str, envelope: MessageEnvelope
    ) -> None:
        user_a_id, _ = sorted([envelope.sender_id, envelope.recipient_id])
        if envelope.recipient_id == user_a_id:
            await self.db.execute(
                "UPDATE conversations "
                "SET last_message_at = ?, unread_count_a = unread_count_a + 1 "
                "WHERE id = ?",
                (envelope.sent_at, conversation_id),
            )
        else:
            await self.db.execute(
                "UPDATE conversations "
                "SET last_message_at = ?, unread_count_b = unread_count_b + 1 "
                "WHERE id = ?",
                (envelope.sent_at, conversation_id),
            )
        await self.db.commit()

    async def _push_if_online(self, envelope: MessageEnvelope) -> int | None:
        delivered_at = None
        pushed = await push_to_user(
            envelope.recipient_id,
            {"type": "message", "payload": envelope.model_dump()},
        )
        if pushed:
            delivered_at = int(time.time())
            await self.db.execute(
                "UPDATE messages SET delivered_at = ? WHERE id = ?",
                (delivered_at, envelope.id),
            )
            await self.db.commit()
        return delivered_at
