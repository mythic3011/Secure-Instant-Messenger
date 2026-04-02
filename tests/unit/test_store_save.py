"""
tests/unit/test_store_save.py — Unit tests for `client/state/store.py` save_message behaviour.

Covers:
- duplicate insert is ignored (idempotent)
- encrypted-at-rest local storage
- missing storage key fails closed
"""

from __future__ import annotations

import logging
import sqlite3
import time

import pytest

from client.state import store


@pytest.mark.asyncio
async def test_round_trip_plaintext_ok(monkeypatch, tmp_path):
    # Redirect home to temp dir so DB is created under tmp_path
    monkeypatch.setattr(store.Path, "home", lambda: tmp_path)

    await store.init_store("testuser_dup")
    # Use a fixed 32-byte storage key for AES-256-GCM
    store.set_storage_key(b"a" * 32)

    msg_id = "duplicate-msg-1"
    await store.save_message(
        id=msg_id,
        conversation_id="conv-dup",
        sender_id="alice",
        recipient_id="bob",
        counter=0,
        plaintext="hello duplicate",
        sent_at=int(time.time()),
        ttl_seconds=None,
    )

    # Second save with same id should be ignored (no exception)
    await store.save_message(
        id=msg_id,
        conversation_id="conv-dup",
        sender_id="alice",
        recipient_id="bob",
        counter=0,
        plaintext="hello duplicate",
        sent_at=int(time.time()),
        ttl_seconds=None,
    )

    msgs = await store.get_messages("conv-dup")
    assert len(msgs) == 1
    assert msgs[0]["plaintext"] == "hello duplicate"

    await store.close_store()


@pytest.mark.asyncio
async def test_db_does_not_contain_plaintext(monkeypatch, tmp_path):
    monkeypatch.setattr(store.Path, "home", lambda: tmp_path)

    await store.init_store("testuser_cipher")
    store.set_storage_key(b"b" * 32)

    plaintext_payload = "top secret ciphertext-only payload"
    await store.save_message(
        id="cipher-msg-1",
        conversation_id="conv-cipher",
        sender_id="alice",
        recipient_id="bob",
        counter=0,
        plaintext=plaintext_payload,
        sent_at=int(time.time()),
        ttl_seconds=None,
    )

    db_path = tmp_path / ".comp3334im" / "testuser_cipher" / "messages.db"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM local_messages WHERE ciphertext LIKE ?",
            (f"%{plaintext_payload}%",),
        ).fetchall()
    finally:
        conn.close()

    assert rows == []
    await store.close_store()


@pytest.mark.asyncio
async def test_missing_storage_key_raises(monkeypatch, tmp_path, caplog):
    # Redirect home to temp dir
    monkeypatch.setattr(store.Path, "home", lambda: tmp_path)

    await store.init_store("testuser_err")

    # Ensure storage key is not set so encryption fails early
    store.set_storage_key(None)

    caplog.set_level(logging.ERROR, logger="client.state.store")

    with pytest.raises(store.LocalStorageSecurityError):
        await store.save_message(
            id="bad-msg-1",
            conversation_id="conv-err",
            sender_id="alice",
            recipient_id="bob",
            counter=0,
            plaintext="this will fail",
            sent_at=int(time.time()),
            ttl_seconds=None,
        )

    # Confirm that unexpected error was logged
    assert any(
        "storage key unavailable for local message encryption" in r.getMessage()
        for r in caplog.records
    )

    await store.close_store()


@pytest.mark.asyncio
async def test_init_store_migrates_plaintext_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(store.Path, "home", lambda: tmp_path)
    store.set_storage_key(b"c" * 32)

    db_path = tmp_path / ".comp3334im" / "legacy_user" / "messages.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE local_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                recipient_id TEXT NOT NULL,
                counter INTEGER NOT NULL,
                plaintext TEXT NOT NULL,
                sent_at INTEGER NOT NULL,
                received_at INTEGER NOT NULL DEFAULT (unixepoch()),
                ttl_seconds INTEGER,
                delivery_status TEXT NOT NULL DEFAULT 'sent',
                UNIQUE(conversation_id, sender_id, counter)
            );
            CREATE TABLE local_conversations (
                id TEXT PRIMARY KEY,
                peer_id TEXT NOT NULL,
                peer_username TEXT NOT NULL,
                last_message_at INTEGER,
                unread_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute(
            """INSERT INTO local_messages
               (id, conversation_id, sender_id, recipient_id, counter, plaintext,
                sent_at, received_at, ttl_seconds, delivery_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-msg-1",
                "conv-legacy",
                "alice",
                "bob",
                0,
                "legacy plaintext",
                int(time.time()),
                int(time.time()),
                None,
                "sent",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    await store.init_store("legacy_user")
    messages = await store.get_messages("conv-legacy")

    assert messages[0]["plaintext"] == "legacy plaintext"

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(local_messages)").fetchall()}
    finally:
        conn.close()

    assert "plaintext" not in columns
    assert {"nonce", "ciphertext"}.issubset(columns)
    await store.close_store()
