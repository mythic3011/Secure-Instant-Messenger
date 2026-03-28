from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

import server.ws.handler as ws_handler
from server.ws.events import build_message_event, build_message_payload


def _fake_message(*, delivered_at: datetime | None = None) -> Any:
    return SimpleNamespace(
        id="msg-1",
        sender_id="alice-id",
        recipient_id="bob-id",
        conversation_id="conv-1",
        counter=0,
        nonce_b64="bm9uY2U=",
        ciphertext_b64="Y2lwaGVydGV4dA==",
        eph_pub_b64="ZXBo",
        conv_dh_pub_b64="Y29udi1kaA==",
        chain_index=7,
        ttl_seconds=30,
        sent_at=datetime(2026, 3, 29, 12, 34, 56, tzinfo=UTC),
        delivery_status=None,
        delivered_at=delivered_at,
    )


def test_build_message_payload_normalizes_datetime_and_preserves_bootstrap_fields() -> None:
    message = _fake_message()

    payload = build_message_payload(cast(Any, message))

    assert payload == {
        "id": "msg-1",
        "type": "message",
        "sender_id": "alice-id",
        "recipient_id": "bob-id",
        "conversation_id": "conv-1",
        "counter": 0,
        "nonce_b64": "bm9uY2U=",
        "ciphertext_b64": "Y2lwaGVydGV4dA==",
        "eph_pub_b64": "ZXBo",
        "conv_dh_pub_b64": "Y29udi1kaA==",
        "chain_index": 7,
        "ttl_seconds": 30,
        "sent_at": 1774787696,
        "delivery_status": "sent",
    }


@pytest.mark.asyncio
async def test_flush_offline_queue_uses_canonical_message_event_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_texts: list[str] = []
    message = _fake_message()

    class _FakeScalars:
        def all(self) -> list[Any]:
            return [message]

    class _FakeResult:
        def scalars(self) -> _FakeScalars:
            return _FakeScalars()

    class _FakeDb:
        async def execute(self, _stmt) -> _FakeResult:
            return _FakeResult()

        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_get_session():
        yield _FakeDb()

    class _FakeWebSocket:
        def __init__(self) -> None:
            self.client = type("Client", (), {"host": "127.0.0.1", "port": 9010})()

        async def send_text(self, raw: str) -> None:
            sent_texts.append(raw)

    monkeypatch.setattr(ws_handler, "get_session", _fake_get_session)

    await ws_handler._flush_offline_queue(cast(Any, _FakeWebSocket()), "bob-id")

    assert len(sent_texts) == 1
    assert json.loads(sent_texts[0]) == build_message_event(cast(Any, message))
