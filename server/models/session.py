"""Session model for authentication tokens."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from server.models.user import User


class Session(Base, TimestampMixin):
    """Authentication session with bearer token."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    revoked: Mapped[bool] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="sessions")

    def __repr__(self) -> str:
        return f"<Session(id={self.id!r}, user_id={self.user_id!r})>"
