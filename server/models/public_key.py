"""Public key bundle model for E2EE key exchange."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models.base import Base

if TYPE_CHECKING:
    from server.models.user import User


class PublicKey(Base):
    """Public key bundle for a user (identity + DH keys)."""

    __tablename__ = "public_keys"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    identity_pub: Mapped[str] = mapped_column(Text, nullable=False)
    dh_pub: Mapped[str] = mapped_column(Text, nullable=False)
    key_sig: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="public_keys")

    def __repr__(self) -> str:
        return f"<PublicKey(user_id={self.user_id!r})>"
