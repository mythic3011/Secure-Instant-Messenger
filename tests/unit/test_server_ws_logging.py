from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

import server.main as server_main


class _FakeWebSocket:
    def __init__(self, receive_result: str | Exception) -> None:
        self.receive_result = receive_result
        self.accepted = False
        self.closed_code: int | None = None
        self.sent_texts: list[str] = []
        self.client = SimpleNamespace(host="127.0.0.1", port=9000)
        self.url = SimpleNamespace(path="/v1/ws")

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if isinstance(self.receive_result, Exception):
            raise self.receive_result
        return self.receive_result

    async def close(self, code: int) -> None:
        self.closed_code = code

    async def send_text(self, raw: str) -> None:
        self.sent_texts.append(raw)


@pytest.mark.asyncio
async def test_ws_endpoint_logs_invalid_auth_frame_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = _FakeWebSocket("{")
    warnings: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        server_main.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    await server_main.ws_endpoint(cast(Any, websocket))

    assert websocket.accepted is True
    assert websocket.closed_code == 4001
    assert warnings == [
        (
            ("ws_auth_rejected",),
            {
                "reason": "invalid_auth_frame_json",
                "client": "127.0.0.1:9000",
                "path": "/v1/ws",
                "close_code": 4001,
            },
        )
    ]


class _FakeResult:
    def __init__(self, user_id: str | None) -> None:
        self._user_id = user_id

    def scalar_one_or_none(self):
        return self._user_id


class _FakeDbSession:
    def __init__(self, user_id: str | None = None) -> None:
        self._user_id = user_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def execute(self, _stmt):
        return _FakeResult(self._user_id)


@pytest.mark.asyncio
async def test_ws_endpoint_logs_invalid_or_expired_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = _FakeWebSocket(json.dumps({"type": "auth", "token": "bad-token"}))
    warnings: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        server_main.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    monkeypatch.setattr(server_main, "get_session", lambda: _FakeDbSession())

    import server.core.security as security

    monkeypatch.setattr(security, "hash_token", lambda token: f"hash:{token}")

    await server_main.ws_endpoint(cast(Any, websocket))

    assert websocket.accepted is True
    assert websocket.closed_code == 4001
    assert warnings == [
        (
            ("ws_auth_rejected",),
            {
                "reason": "invalid_or_expired_token",
                "client": "127.0.0.1:9000",
                "path": "/v1/ws",
                "close_code": 4001,
            },
        )
    ]


@pytest.mark.asyncio
async def test_ws_endpoint_sends_auth_ok_before_entering_session_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = _FakeWebSocket(json.dumps({"type": "auth", "token": "good-token"}))
    forwarded: list[tuple[object, str]] = []

    monkeypatch.setattr(server_main, "get_session", lambda: _FakeDbSession("user-123"))

    import server.core.security as security

    monkeypatch.setattr(security, "hash_token", lambda token: f"hash:{token}")

    async def _fake_websocket_endpoint(ws, user_id: str) -> None:
        forwarded.append((ws, user_id))

    monkeypatch.setattr(server_main, "websocket_endpoint", _fake_websocket_endpoint)

    await server_main.ws_endpoint(cast(Any, websocket))

    assert websocket.sent_texts == ['{"type": "auth_ok"}']
    assert forwarded == [(websocket, "user-123")]
