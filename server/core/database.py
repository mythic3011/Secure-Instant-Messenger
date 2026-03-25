"""
server/core/database.py — aiosqlite setup, connection pool, and migrations.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite

from server.core.config import get_settings

logger = logging.getLogger(__name__)

_db: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
    """Return the shared DB connection. Call init_db() at startup first."""
    if _db is None:
        raise RuntimeError("Database not initialised. Call init_db() at startup.")
    return _db


async def init_db() -> None:
    """Open the SQLite connection and run migrations."""
    global _db
    settings = get_settings()

    # Extract file path from URL: sqlite+aiosqlite:////app/data/im.db → /app/data/im.db
    db_path = settings.database_url.split("///")[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row

    # Enable WAL mode and foreign keys on every connection
    await _db.execute("PRAGMA journal_mode = WAL")
    await _db.execute("PRAGMA foreign_keys = ON")
    await _db.execute("PRAGMA secure_delete = ON")

    await _run_migrations(_db)
    logger.info("Database initialised: %s", db_path)


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Apply SQL migration files in order."""
    migrations_dir = Path(__file__).parent.parent / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    await db.execute(
        "CREATE TABLE IF NOT EXISTS _migrations "
        "(filename TEXT PRIMARY KEY, applied_at INTEGER NOT NULL DEFAULT (unixepoch()))"
    )
    await db.commit()

    for mf in migration_files:
        async with db.execute(
            "SELECT 1 FROM _migrations WHERE filename = ?", (mf.name,)
        ) as cur:
            if await cur.fetchone():
                continue  # already applied

        logger.info("Applying migration: %s", mf.name)
        sql = mf.read_text()
        await db.executescript(sql)
        await db.execute("INSERT INTO _migrations (filename) VALUES (?)", (mf.name,))
        await db.commit()
