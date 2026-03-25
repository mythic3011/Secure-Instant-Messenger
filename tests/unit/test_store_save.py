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

    secret = "top secret ciphertext-only payload"
    await store.save_message(
        id="cipher-msg-1",
        conversation_id="conv-cipher",
        sender_id="alice",
        recipient_id="bob",
        counter=0,
        plaintext=secret,
        sent_at=int(time.time()),
        ttl_seconds=None,
    )

    db_path = tmp_path / ".comp3334im" / "testuser_cipher" / "messages.db"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM local_messages WHERE ciphertext LIKE ?",
            (f"%{secret}%",),
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
