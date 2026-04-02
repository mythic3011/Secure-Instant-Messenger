"""Friend request model for managing friend relationships."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from server.models.user import User


class FriendRequestStatus(StrEnum):
    """Status of a friend request."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class FriendRequest(Base, TimestampMixin):
    """Friend request between two users."""

    __tablename__ = "friend_requests"
    __table_args__ = (
        UniqueConstraint("sender_id", "recipient_id", name="uq_friend_request_sender_recipient"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sender_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[FriendRequestStatus] = mapped_column(
        Enum(FriendRequestStatus), default=FriendRequestStatus.PENDING, nullable=False
    )

    # Relationships
    sender: Mapped[User] = relationship(
        "User", foreign_keys=[sender_id], back_populates="sent_friend_requests"
    )
    recipient: Mapped[User] = relationship(
        "User", foreign_keys=[recipient_id], back_populates="received_friend_requests"
    )

    def __repr__(self) -> str:
        return (
            f"<FriendRequest(id={self.id!r}, sender={self.sender_id!r}, "
            f"recipient={self.recipient_id!r}, status={self.status!r})>"
        )
