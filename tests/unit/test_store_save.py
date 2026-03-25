"""
tests/unit/test_store_save.py — Unit tests for `client/state/store.py` save_message behaviour.

Covers:
- duplicate insert is ignored (idempotent)
- unexpected errors (missing storage key) are logged and propagated
"""

from __future__ import annotations

import logging
import time

import pytest

from client.state import store


@pytest.mark.asyncio
async def test_save_message_duplicate_ignored(monkeypatch, tmp_path):
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
async def test_save_message_unexpected_error_propagated(monkeypatch, tmp_path, caplog):
    # Redirect home to temp dir
    monkeypatch.setattr(store.Path, "home", lambda: tmp_path)

    await store.init_store("testuser_err")

    # Ensure storage key is not set so encryption fails early
    store._storage_key = None

    caplog.set_level(logging.ERROR, logger="client.state.store")

    with pytest.raises(RuntimeError):
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
    assert any("unexpected error storing message id bad-msg-1" in r.getMessage() for r in caplog.records)

    await store.close_store()
