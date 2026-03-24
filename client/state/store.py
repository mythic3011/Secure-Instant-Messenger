"""
client/state/store.py — Local SQLite store for message history, session state,
and conversation metadata. All message content is stored as ciphertext only;
plaintext is never written to disk.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from sqlalchemy import Column, Integer, String, Text, DateTime, func, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

# Base class for local models
class LocalBase(DeclarativeBase):
    pass


class LocalMessage(LocalBase):
    """Local message storage with encrypted plaintext."""

    __tablename__ = "local_messages"

    id = Column(String(36), primary_key=True)
    conversation_id = Column(String(32), nullable=False, index=True)
    sender_id = Column(String(32), nullable=False)
    recipient_id = Column(String(32), nullable=False)
    counter = Column(Integer, nullable=False)
    ciphertext_b64 = Column(Text, nullable=False)  # AES-GCM encrypted plaintext
    nonce_b64 = Column(Text, nullable=False)  # AES-GCM nonce
    sent_at = Column(Integer, nullable=False)
    received_at = Column(Integer, nullable=False, default=func.unixepoch())
    ttl_seconds = Column(Integer, nullable=True)
    expires_at = Column(Integer, nullable=True)
    delivery_status = Column(String(20), nullable=False, default="sent")


class LocalConversation(LocalBase):
    """Local conversation metadata."""

    __tablename__ = "local_conversations"

    id = Column(String(32), primary_key=True)
    peer_id = Column(String(32), nullable=False)
    peer_username = Column(String(255), nullable=False)
    last_message_at = Column(Integer, nullable=True)
    unread_count = Column(Integer, nullable=False, default=0)


_engine = None
_session_factory = None
_storage_key: bytes | None = None


def _get_storage_key() -> bytes:
    """Get the storage key for encrypting/decrypting messages."""
    if _storage_key is None:
        raise RuntimeError("Storage key not set. Call set_storage_key() first.")
    return _storage_key


def set_storage_key(key: bytes) -> None:
    """Set the storage key for encrypting/decrypting messages."""
    global _storage_key
    _storage_key = key


def _aes_encrypt(plaintext: bytes) -> tuple[bytes, bytes]:
    """Encrypt plaintext with AES-256-GCM. Returns (ciphertext_with_tag, nonce)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(_get_storage_key()).encrypt(nonce, plaintext, None)
    return ct, nonce


def _aes_decrypt(ciphertext: bytes, nonce: bytes) -> bytes:
    """Decrypt AES-256-GCM ciphertext. Raises InvalidTag on failure."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(_get_storage_key()).decrypt(nonce, ciphertext, None)


async def init_store(username: str) -> None:
    """Open (or create) the local message store for this user."""
    global _engine, _session_factory

    db_path = Path.home() / ".comp3334im" / username / "messages.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite+aiosqlite:///{db_path}"
    _engine = create_async_engine(url, echo=False)

    # Enable WAL mode and foreign keys
    @event.listens_for(_engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA secure_delete=ON")
        cursor.close()

    _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    # Create all tables
    async with _engine.begin() as conn:
        await conn.run_sync(LocalBase.metadata.create_all)

    # Sweep expired messages on startup (R11)
    await sweep_expired()


async def close_store() -> None:
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def _get_session() -> AsyncSession:
    if _session_factory is None:
        raise RuntimeError("Store not initialised. Call init_store() first.")
    return _session_factory()


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
    """Persist an encrypted message to local storage."""
    async with _get_session() as session:
        try:
            # Encrypt plaintext before storing
            ciphertext, nonce = _aes_encrypt(plaintext.encode("utf-8"))
            message = LocalMessage(
                id=id,
                conversation_id=conversation_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                counter=counter,
                ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
                nonce_b64=base64.b64encode(nonce).decode("ascii"),
                sent_at=sent_at,
                ttl_seconds=ttl_seconds,
                delivery_status=delivery_status,
            )
            session.add(message)
            await session.commit()
        except Exception:
            pass  # duplicate — already stored


async def update_delivery_status(message_id: str, status: str) -> None:
    async with _get_session() as session:
        from sqlalchemy import update
        stmt = update(LocalMessage).where(LocalMessage.id == message_id).values(delivery_status=status)
        await session.execute(stmt)
        await session.commit()


async def get_messages(
    conversation_id: str,
    limit: int = 50,
    before_sent_at: int | None = None,
) -> list[dict]:
    """Fetch local messages for a conversation, newest first."""
    async with _get_session() as session:
        from sqlalchemy import select
        now = int(time.time())

        if before_sent_at:
            stmt = (
                select(LocalMessage)
                .where(
                    LocalMessage.conversation_id == conversation_id,
                    LocalMessage.sent_at < before_sent_at,
                    (LocalMessage.expires_at.is_(None) | (LocalMessage.expires_at > now)),
                )
                .order_by(LocalMessage.sent_at.desc())
                .limit(limit)
            )
        else:
            stmt = (
                select(LocalMessage)
                .where(
                    LocalMessage.conversation_id == conversation_id,
                    (LocalMessage.expires_at.is_(None) | (LocalMessage.expires_at > now)),
                )
                .order_by(LocalMessage.sent_at.desc())
                .limit(limit)
            )

        result = await session.execute(stmt)
        messages = result.scalars().all()

        # Decrypt messages
        decrypted_messages = []
        for msg in messages:
            try:
                ciphertext = base64.b64decode(msg.ciphertext_b64)
                nonce = base64.b64decode(msg.nonce_b64)
                plaintext = _aes_decrypt(ciphertext, nonce).decode("utf-8")
            except Exception:
                plaintext = "[decryption failed]"

            decrypted_messages.append({
                "id": msg.id,
                "conversation_id": msg.conversation_id,
                "sender_id": msg.sender_id,
                "recipient_id": msg.recipient_id,
                "counter": msg.counter,
                "plaintext": plaintext,
                "sent_at": msg.sent_at,
                "received_at": msg.received_at,
                "ttl_seconds": msg.ttl_seconds,
                "expires_at": msg.expires_at,
                "delivery_status": msg.delivery_status,
            })

        return decrypted_messages


async def sweep_expired() -> int:
    """Delete expired messages from local storage (R11). Returns count deleted."""
    async with _get_session() as session:
        from sqlalchemy import delete
        now = int(time.time())
        stmt = delete(LocalMessage).where(
            LocalMessage.expires_at.isnot(None),
            LocalMessage.expires_at <= now,
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount


async def upsert_conversation(
    *,
    id: str,
    peer_id: str,
    peer_username: str,
    last_message_at: int | None,
    unread_count: int,
) -> None:
    async with _get_session() as session:
        from sqlalchemy import select
        stmt = select(LocalConversation).where(LocalConversation.id == id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.last_message_at = last_message_at
            existing.unread_count = unread_count
        else:
            conversation = LocalConversation(
                id=id,
                peer_id=peer_id,
                peer_username=peer_username,
                last_message_at=last_message_at,
                unread_count=unread_count,
            )
            session.add(conversation)

        await session.commit()


async def get_conversations() -> list[dict]:
    async with _get_session() as session:
        from sqlalchemy import select
        stmt = select(LocalConversation).order_by(LocalConversation.last_message_at.desc().nulls_last())
        result = await session.execute(stmt)
        conversations = result.scalars().all()

        return [
            {
                "id": conv.id,
                "peer_id": conv.peer_id,
                "peer_username": conv.peer_username,
                "last_message_at": conv.last_message_at,
                "unread_count": conv.unread_count,
            }
            for conv in conversations
        ]


async def increment_unread(conversation_id: str) -> None:
    async with _get_session() as session:
        from sqlalchemy import update
        stmt = update(LocalConversation).where(LocalConversation.id == conversation_id).values(unread_count=LocalConversation.unread_count + 1)
        await session.execute(stmt)
        await session.commit()


async def reset_unread(conversation_id: str) -> None:
    async with _get_session() as session:
        from sqlalchemy import update
        stmt = update(LocalConversation).where(LocalConversation.id == conversation_id).values(unread_count=0)
        await session.execute(stmt)
        await session.commit()
