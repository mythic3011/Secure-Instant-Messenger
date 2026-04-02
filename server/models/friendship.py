"""Friendship model for mutual friend relationships."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base


class Friendship(Base):
    """Mutual friendship between two users (canonical order: user_a_id < user_b_id)."""

    __tablename__ = "friendships"
    __table_args__ = (CheckConstraint("user_a_id < user_b_id", name="ck_friendship_order"),)

    user_a_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    user_b_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<Friendship(user_a={self.user_a_id!r}, user_b={self.user_b_id!r})>"
