from __future__ import annotations

import asyncio
import ssl
from types import SimpleNamespace

import pytest
import websockets.exceptions

from client.api import client as client_module


def test_tls_default_verifies_cert() -> None:
    verify, ws_ssl = client_module.apply_tls_policy()

    assert verify is True
    assert ws_ssl is None


def test_tls_insecure_flag_disables_verify_with_warning(monkeypatch) -> None:
    warnings: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        client_module.log,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )

    verify, ws_ssl = client_module.apply_tls_policy(verify_tls=False)

    assert verify is False
    assert isinstance(ws_ssl, ssl.SSLContext)
    assert warnings


class _FakeWsContext:
    def __init__(self, ws) -> None:
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *_args) -> None:
        return None


class _FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def recv(self):
        raise websockets.exceptions.ConnectionClosedError(
            None,
            SimpleNamespace(code=4001, reason="unauthorized"),
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_connect_ws_raises_when_initial_auth_fails(monkeypatch) -> None:
    client = client_module.IMClient("https://example.test")
    client._token = "token"
    fake_ws = _FakeWs()

    async with client:
        monkeypatch.setattr(
            client_module.websockets,
            "connect",
            lambda *_args, **_kwargs: _FakeWsContext(fake_ws),
        )

        with pytest.raises(
            client_module.IMClientConnectionError,
            match="WebSocket authentication failed.",
        ):
            await client.connect_ws(lambda _msg: asyncio.sleep(0))

        assert client._ws_task is None
