"""Message model for encrypted message storage."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models.base import Base

if TYPE_CHECKING:
    from server.models.user import User


class Message(Base):
    """Encrypted message stored on server (ciphertext only)."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sender_id", "counter", name="uq_message_replay"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID v4
    conversation_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), nullable=False)
    counter: Mapped[int] = mapped_column(Integer, nullable=False)
    nonce_b64: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext_b64: Mapped[str] = mapped_column(Text, nullable=False)
    eph_pub_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    conv_dh_pub_b64: Mapped[str | None] = mapped_column(Text, nullable=True)
    chain_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    stored_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    sender: Mapped[User] = relationship("User", foreign_keys=[sender_id])
    recipient: Mapped[User] = relationship("User", foreign_keys=[recipient_id])

    def __repr__(self) -> str:
        return f"<Message(id={self.id!r}, conversation={self.conversation_id!r})>"
