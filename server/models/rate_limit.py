"""Rate limit model for API rate limiting."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import Base


class RateLimit(Base):
    """Rate limit tracking for API endpoints."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    window_start: Mapped[int] = mapped_column(Integer, nullable=False)
    locked_until: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return f"<RateLimit(key={self.key!r}, attempts={self.attempts})>"
