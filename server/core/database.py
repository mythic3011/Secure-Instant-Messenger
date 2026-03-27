"""
server/core/database.py — SQLAlchemy async setup with auto-detection for database dialects.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.core.config import get_settings
from server.models.base import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory = None


def get_database_url() -> str:
    """Get database URL from settings with auto-detection of dialect."""
    settings = get_settings()
    url = settings.database_url

    # Auto-detect and convert to async URL if needed
    if url.startswith("sqlite:///"):
        # Convert sqlite:/// to sqlite+aiosqlite:///
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    elif url.startswith("postgresql://"):
        # Convert postgresql:// to postgresql+asyncpg://
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("mysql://"):
        # Convert mysql:// to mysql+aiomysql://
        url = url.replace("mysql://", "mysql+aiomysql://", 1)

    return url


def get_engine_kwargs() -> dict[str, Any]:
    """Get engine kwargs based on database dialect."""
    url = get_database_url()
    kwargs: dict[str, Any] = {"echo": False, "future": True}

    if "sqlite" in url:
        # SQLite-specific settings
        kwargs["connect_args"] = {"check_same_thread": False}
    elif "postgresql" in url:
        # PostgreSQL-specific settings
        kwargs["pool_size"] = 20
        kwargs["max_overflow"] = 10
        kwargs["pool_pre_ping"] = True
    elif "mysql" in url:
        # MySQL-specific settings
        kwargs["pool_size"] = 20
        kwargs["max_overflow"] = 10
        kwargs["pool_pre_ping"] = True

    return kwargs


async def init_db() -> None:
    """Initialize the database engine and session factory."""
    global _engine, _session_factory

    url = get_database_url()
    kwargs = get_engine_kwargs()

    _engine = create_async_engine(url, **kwargs)
    _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    # Enable WAL mode and foreign keys for SQLite
    if "sqlite" in url:

        @event.listens_for(_engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA secure_delete=ON")
            cursor.close()

    # Create all tables
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialised: %s", url)


async def close_db() -> None:
    """Close the database engine."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def get_session():
    """Get an async database session."""
    if _session_factory is None:
        raise RuntimeError("Database not initialised. Call init_db() at startup.")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db():
    """Get a database session for dependency injection."""
    if _session_factory is None:
        raise RuntimeError("Database not initialised. Call init_db() at startup.")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
