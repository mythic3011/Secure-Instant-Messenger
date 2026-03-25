from __future__ import annotations

"""
client/state/store.py — Local SQLite store for message history, session state,
and conversation metadata. Message bodies are encrypted at rest; plaintext is
never written to disk.
"""

import logging
import os
import time
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from client.crypto.session import LocalStorageSecurityError
from client.crypto.storage import get_storage_key, set_active_storage_key

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS local_conversations (
    id              TEXT PRIMARY KEY,
    peer_id         TEXT NOT NULL,
    peer_username   TEXT NOT NULL,
    last_message_at INTEGER,
    unread_count    INTEGER NOT NULL DEFAULT 0
);
"""

_LOCAL_MESSAGES_TABLE_SQL = """
CREATE TABLE local_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sender_id       TEXT NOT NULL,
    recipient_id    TEXT NOT NULL,
    counter         INTEGER NOT NULL,
    nonce           BLOB NOT NULL,
    ciphertext      BLOB NOT NULL,
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
)
"""

log = logging.getLogger("client.state.store")

_db: aiosqlite.Connection | None = None
MessageRow = dict[str, Any]


def set_storage_key(key: bytes | None) -> None:
    """Backward-compatible test hook for the active storage key."""
    set_active_storage_key(key)


def _aad(conversation_id: str, message_id: str) -> bytes:
    return f"{conversation_id}:{message_id}".encode("utf-8")


def _require_storage_key() -> bytes:
    try:
        return get_storage_key()
    except LocalStorageSecurityError as exc:
        log.error("storage key unavailable for local message encryption")
        raise


def _encrypt_body(plaintext: str, *, key: bytes, aad: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return nonce, ciphertext


def _decrypt_body(nonce: bytes, ciphertext: bytes, *, key: bytes, aad: bytes) -> str:
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except Exception as exc:
        raise LocalStorageSecurityError(
            "Failed to decrypt local message from encrypted store"
        ) from exc
    return plaintext.decode("utf-8")


async def init_store(username: str) -> None:
    """Open (or create) the local message store for this user."""
    global _db
    db_path = Path.home() / ".comp3334im" / username / "messages.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    await _db.executescript(_SCHEMA)
    await _ensure_message_schema(_db)
    await _create_message_indexes(_db)
    await _db.commit()
    await sweep_expired()


async def _ensure_message_schema(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(local_messages)") as cur:
        rows = await cur.fetchall()
    columns = {row["name"] for row in rows}
    if not columns:
        await db.execute(_LOCAL_MESSAGES_TABLE_SQL)
        return
    if {"nonce", "ciphertext"}.issubset(columns) and "plaintext" not in columns:
        return
    if "plaintext" in columns and not {"nonce", "ciphertext"}.intersection(columns):
        await _migrate_plaintext_messages(db)
        return
    raise LocalStorageSecurityError(
        f"Unsupported local_messages schema columns: {sorted(columns)}"
    )


async def _migrate_plaintext_messages(db: aiosqlite.Connection) -> None:
    key = _require_storage_key()
    async with db.execute(
        """SELECT id, conversation_id, sender_id, recipient_id, counter, plaintext,
                  sent_at, received_at, ttl_seconds, delivery_status
           FROM local_messages"""
    ) as cur:
        old_rows = await cur.fetchall()

    await db.execute("ALTER TABLE local_messages RENAME TO local_messages_legacy")
    await db.execute(_LOCAL_MESSAGES_TABLE_SQL)

    for row in old_rows:
        aad = _aad(row["conversation_id"], row["id"])
        nonce, ciphertext = _encrypt_body(row["plaintext"], key=key, aad=aad)
        await db.execute(
            """INSERT INTO local_messages
               (id, conversation_id, sender_id, recipient_id, counter,
                nonce, ciphertext, sent_at, received_at, ttl_seconds, delivery_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["conversation_id"],
                row["sender_id"],
                row["recipient_id"],
                row["counter"],
                nonce,
                ciphertext,
                row["sent_at"],
                row["received_at"],
                row["ttl_seconds"],
                row["delivery_status"],
            ),
        )

    await db.execute("DROP TABLE local_messages_legacy")


async def _create_message_indexes(db: aiosqlite.Connection) -> None:
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_lm_conv ON local_messages(conversation_id, sent_at DESC)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_lm_expires ON local_messages(expires_at) WHERE expires_at IS NOT NULL"
    )


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
    db = _get_db()
    key = _require_storage_key()
    aad = _aad(conversation_id, id)

    try:
        nonce, ciphertext = _encrypt_body(plaintext, key=key, aad=aad)
        await db.execute(
            """INSERT OR IGNORE INTO local_messages
               (id, conversation_id, sender_id, recipient_id, counter,
                nonce, ciphertext, sent_at, ttl_seconds, delivery_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                id,
                conversation_id,
                sender_id,
                recipient_id,
                counter,
                nonce,
                ciphertext,
                sent_at,
                ttl_seconds,
                delivery_status,
            ),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        return
    except LocalStorageSecurityError:
        raise
    except Exception as exc:
        log.error("unexpected error storing message id %s", id, exc_info=exc)
        raise RuntimeError(f"failed to store message {id}") from exc


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
) -> list[MessageRow]:
    """Fetch local messages for a conversation, newest first."""
    db = _get_db()
    now = int(time.time())
    key = _require_storage_key()

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

    result: list[MessageRow] = []
    for row in rows:
        record = dict(row)
        aad = _aad(record["conversation_id"], record["id"])
        record["plaintext"] = _decrypt_body(
            record["nonce"],
            record["ciphertext"],
            key=key,
            aad=aad,
        )
        result.append(record)
    return result


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


async def get_conversations() -> list[MessageRow]:
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
