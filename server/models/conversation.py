"""Conversation model for chat metadata."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base


class Conversation(Base):
    """Conversation metadata between two users (canonical order: user_a_id < user_b_id)."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_conversation_users"),
        CheckConstraint("user_a_id < user_b_id", name="ck_conversation_order"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_a_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    user_b_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unread_count_a: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unread_count_b: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id!r}, user_a={self.user_a_id!r}, user_b={self.user_b_id!r})>"
