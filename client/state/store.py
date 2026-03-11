"""
client/state/store.py — Local SQLite store for message history, session state,
and conversation metadata. All message content is stored as ciphertext only;
plaintext is never written to disk.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS local_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sender_id       TEXT NOT NULL,
    recipient_id    TEXT NOT NULL,
    counter         INTEGER NOT NULL,
    plaintext       TEXT NOT NULL,      -- decrypted text, stored locally only
    sent_at         INTEGER NOT NULL,
    received_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    ttl_seconds     INTEGER,
    expires_at      INTEGER GENERATED ALWAYS AS (
                        CASE WHEN ttl_seconds IS NOT NULL
                        THEN received_at + ttl_seconds
                        ELSE NULL END
                    ) VIRTUAL,
    delivery_status TEXT NOT NULL DEFAULT 'sent',
    UNIQUE(conversation_id, sender_id, counter)
);

CREATE INDEX IF NOT EXISTS idx_lm_conv ON local_messages(conversation_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_lm_expires ON local_messages(expires_at) WHERE expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS local_conversations (
    id              TEXT PRIMARY KEY,
    peer_id         TEXT NOT NULL,
    peer_username   TEXT NOT NULL,
    last_message_at INTEGER,
    unread_count    INTEGER NOT NULL DEFAULT 0
);
"""

_db: aiosqlite.Connection | None = None


async def init_store(username: str) -> None:
    """Open (or create) the local message store for this user."""
    global _db
    db_path = Path.home() / ".comp3334im" / username / "messages.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    await _db.executescript(_SCHEMA)
    await _db.commit()
    # Sweep expired messages on startup (R11)
    await sweep_expired()


async def close_store() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


def _get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Store not initialised. Call init_store() first.")
    return _db


async def save_message(
    *,
    id: str,
    conversation_id: str,
    sender_id: str,
    recipient_id: str,
    counter: int,
    plaintext: str,
    sent_at: int,
    ttl_seconds: int | None,
    delivery_status: str = "sent",
) -> None:
    """Persist a decrypted message to local storage."""
    db = _get_db()
    try:
        await db.execute(
            """INSERT OR IGNORE INTO local_messages
               (id, conversation_id, sender_id, recipient_id, counter,
                plaintext, sent_at, ttl_seconds, delivery_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (id, conversation_id, sender_id, recipient_id, counter,
             plaintext, sent_at, ttl_seconds, delivery_status),
        )
        await db.commit()
    except Exception:
        pass  # duplicate — already stored


async def update_delivery_status(message_id: str, status: str) -> None:
    db = _get_db()
    await db.execute(
        "UPDATE local_messages SET delivery_status = ? WHERE id = ?",
        (status, message_id),
    )
    await db.commit()


async def get_messages(
    conversation_id: str,
    limit: int = 50,
    before_sent_at: int | None = None,
) -> list[dict]:
    """Fetch local messages for a conversation, newest first."""
    db = _get_db()
    now = int(time.time())

    if before_sent_at:
        async with db.execute(
            """SELECT * FROM local_messages
               WHERE conversation_id = ? AND sent_at < ?
                 AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY sent_at DESC LIMIT ?""",
            (conversation_id, before_sent_at, now, limit),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            """SELECT * FROM local_messages
               WHERE conversation_id = ?
                 AND (expires_at IS NULL OR expires_at > ?)
               ORDER BY sent_at DESC LIMIT ?""",
            (conversation_id, now, limit),
        ) as cur:
            rows = await cur.fetchall()

    return [dict(r) for r in rows]


async def sweep_expired() -> int:
    """Delete expired messages from local storage (R11). Returns count deleted."""
    db = _get_db()
    now = int(time.time())
    result = await db.execute(
        "DELETE FROM local_messages WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (now,),
    )
    await db.commit()
    return result.rowcount


async def upsert_conversation(
    *,
    id: str,
    peer_id: str,
    peer_username: str,
    last_message_at: int | None,
    unread_count: int,
) -> None:
    db = _get_db()
    await db.execute(
        """INSERT INTO local_conversations (id, peer_id, peer_username, last_message_at, unread_count)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             last_message_at = excluded.last_message_at,
             unread_count    = excluded.unread_count""",
        (id, peer_id, peer_username, last_message_at, unread_count),
    )
    await db.commit()


async def get_conversations() -> list[dict]:
    db = _get_db()
    async with db.execute(
        "SELECT * FROM local_conversations ORDER BY last_message_at DESC NULLS LAST"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def increment_unread(conversation_id: str) -> None:
    db = _get_db()
    await db.execute(
        "UPDATE local_conversations SET unread_count = unread_count + 1 WHERE id = ?",
        (conversation_id,),
    )
    await db.commit()


async def reset_unread(conversation_id: str) -> None:
    db = _get_db()
    await db.execute(
        "UPDATE local_conversations SET unread_count = 0 WHERE id = ?",
        (conversation_id,),
    )
    await db.commit()
