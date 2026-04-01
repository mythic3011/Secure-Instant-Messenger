"""Delivery state transitions for message acknowledgements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import Message


@dataclass(frozen=True)
class DeliveryAckResult:
    status: Literal["not_found", "not_recipient", "already_delivered", "delivered"]
    sender_id: str | None = None
    delivered_at_ts: int | None = None


async def mark_delivered_on_ack(
    db: AsyncSession,
    *,
    message_id: str,
    actor_id: str,
) -> DeliveryAckResult:
    stmt = (
        select(Message.sender_id, Message.recipient_id, Message.delivered_at)
        .where(Message.id == message_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        return DeliveryAckResult(status="not_found")

    if row.recipient_id != actor_id:
        return DeliveryAckResult(status="not_recipient")

    if row.delivered_at is not None:
        return DeliveryAckResult(status="already_delivered", sender_id=row.sender_id)

    now_dt = datetime.now(UTC)
    await db.execute(update(Message).where(Message.id == message_id).values(delivered_at=now_dt))
    await db.commit()
    return DeliveryAckResult(
        status="delivered",
        sender_id=row.sender_id,
        delivered_at_ts=int(now_dt.timestamp()),
    )
