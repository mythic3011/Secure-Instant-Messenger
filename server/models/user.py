"""User model for authentication and profile data."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from server.models.friend_request import FriendRequest
    from server.models.public_key import PublicKey
    from server.models.session import Session


class User(Base, TimestampMixin, SoftDeleteMixin):
    """User account model."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    pw_hash: Mapped[str] = mapped_column(Text, nullable=False)
    totp_secret: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    public_keys: Mapped["PublicKey | None"] = relationship(
        "PublicKey", back_populates="user", uselist=False, lazy="selectin"
    )
    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", lazy="selectin"
    )
    sent_friend_requests: Mapped[list["FriendRequest"]] = relationship(
        "FriendRequest",
        foreign_keys="FriendRequest.sender_id",
        back_populates="sender",
        lazy="selectin",
    )
    received_friend_requests: Mapped[list["FriendRequest"]] = relationship(
        "FriendRequest",
        foreign_keys="FriendRequest.recipient_id",
        back_populates="recipient",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id!r}, username={self.username!r})>"
