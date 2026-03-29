from __future__ import annotations

from typing import Any, cast

import pytest

import server.ws.handler as ws_handler


class _FailingSendWebSocket:
    def __init__(self) -> None:
        self.client = type("Client", (), {"host": "127.0.0.1", "port": 9001})()

    async def send_text(self, _raw: str) -> None:
        raise RuntimeError("socket write failed")


class _LoopFailureWebSocket:
    def __init__(self) -> None:
        self.client = type("Client", (), {"host": "127.0.0.1", "port": 9002})()

    async def receive_text(self) -> str:
        raise RuntimeError("receive exploded")

    async def send_text(self, _raw: str) -> None:
        return None


@pytest.mark.asyncio
async def test_push_to_user_logs_runtime_context_on_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        ws_handler.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    async with ws_handler._lock:
        ws_handler._connections["user-1"] = cast(Any, _FailingSendWebSocket())

    delivered = await ws_handler.push_to_user("user-1", {"type": "message"})

    assert delivered is False
    assert warnings == [
        (
            ("ws_push_failed",),
            {
                "user_id": "user-1",
                "phase": "push_delivery",
                "client": "127.0.0.1:9001",
                "error": "socket write failed",
                "error_type": "RuntimeError",
            },
        )
    ]
    async with ws_handler._lock:
        assert "user-1" not in ws_handler._connections


@pytest.mark.asyncio
async def test_websocket_endpoint_logs_runtime_loop_failure_with_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple[tuple, dict]] = []
    infos: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        ws_handler.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    monkeypatch.setattr(
        ws_handler.log,
        "info",
        lambda *args, **kwargs: infos.append((args, kwargs)),
    )

    async def _no_offline_flush(_websocket, _user_id: str) -> None:
        return None

    monkeypatch.setattr(ws_handler, "_flush_offline_queue", _no_offline_flush)
    monkeypatch.setattr(
        ws_handler.asyncio,
        "wait_for",
        lambda awaitable, timeout: awaitable,
    )

    websocket = _LoopFailureWebSocket()

    await ws_handler.websocket_endpoint(cast(Any, websocket), "user-2")

    assert warnings == [
        (
            ("ws_error",),
            {
                "user_id": "user-2",
                "phase": "session_loop",
                "client": "127.0.0.1:9002",
                "error": "receive exploded",
                "error_type": "RuntimeError",
            },
        )
    ]
    assert infos[0] == (("ws_connected",), {"user_id": "user-2", "client": "127.0.0.1:9002"})
    assert infos[-1] == (("ws_disconnected",), {"user_id": "user-2", "client": "127.0.0.1:9002"})
    async with ws_handler._lock:
        assert "user-2" not in ws_handler._connections
