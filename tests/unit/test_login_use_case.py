from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from client.api.client import IMClientConnectionError, IMClientError
from client.use_cases.login import (
    LoginClientProtocol,
    LoginContext,
    LoginFailed,
    LoginSucceeded,
    execute_login_handshake,
)
from shared.protocol import LoginRequest
from tests.secrets import TEST_ACCOUNT_PASSWORD


def _imclient_error(status_code: int, detail: str) -> IMClientError:
    return IMClientError(status_code, f'{{"detail":"{detail}"}}')


async def _on_ws_message(_payload: dict[Any, Any]) -> None:
    return None


def _client_factory(fake_client: _FakeClient) -> LoginClientProtocol:
    return cast(LoginClientProtocol, fake_client)


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.logged_in = False
        self.connected = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        self.closed = True

    async def login(self, _body: LoginRequest) -> object:
        self.logged_in = True
        return object()

    async def get_keys(self, _username: str) -> Any:
        return SimpleNamespace(user_id="alice-id")

    async def connect_ws(self, _on_message: Any) -> None:
        self.connected = True


@pytest.mark.asyncio
async def test_execute_login_handshake_returns_success() -> None:
    fake_client = _FakeClient()
    derived: list[tuple[str, str]] = []
    init_calls: list[str] = []
    password = TEST_ACCOUNT_PASSWORD
    sessions = {"conv-1": object()}

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda username: username == "alice",
        load_keystore_fn=lambda *_args, **_kwargs: object(),
        derive_storage_key_fn=lambda username, password: derived.append((username, password)),
        init_store_fn=lambda username: init_calls.append(username) or _async_noop(),
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: sessions,
    )

    result = await execute_login_handshake(
        context,
        username="alice",
        password=password,
        totp_code="123456",
    )

    assert isinstance(result, LoginSucceeded)
    assert result.client is fake_client
    assert result.username == "alice"
    assert result.password == password
    assert result.user_id == "alice-id"
    assert result.sessions is sessions
    assert fake_client.logged_in is True
    assert fake_client.connected is True
    assert derived == [("alice", password)]
    assert init_calls == ["alice"]


@pytest.mark.asyncio
async def test_execute_login_handshake_returns_failure_on_initial_websocket_error() -> None:
    fake_client = _FakeClient()
    password = TEST_ACCOUNT_PASSWORD

    async def _connect_ws(_on_message) -> None:
        raise IMClientConnectionError("WebSocket authentication failed.")

    fake_client.connect_ws = cast(Any, _connect_ws)

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda _username: True,
        load_keystore_fn=lambda *_args, **_kwargs: object(),
        derive_storage_key_fn=lambda *_args, **_kwargs: None,
        init_store_fn=lambda *_args, **_kwargs: _async_noop(),
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: {},
    )

    result = await execute_login_handshake(
        context,
        username="alice",
        password=password,
        totp_code="123456",
    )

    assert result == LoginFailed(message="Login failed: WebSocket authentication failed.")
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_execute_login_handshake_returns_network_failure_message() -> None:
    fake_client = _FakeClient()
    password = TEST_ACCOUNT_PASSWORD

    async def _login(_body) -> None:
        raise httpx.ConnectError("offline")

    fake_client.login = cast(Any, _login)

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda _username: True,
        load_keystore_fn=lambda *_args, **_kwargs: object(),
        derive_storage_key_fn=lambda *_args, **_kwargs: None,
        init_store_fn=lambda *_args, **_kwargs: _async_noop(),
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: {},
    )

    result = await execute_login_handshake(
        context,
        username="alice",
        password=password,
        totp_code="123456",
    )

    assert result == LoginFailed(message="Cannot reach server — is it running? (ConnectError)")
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_execute_login_handshake_closes_client_when_keystore_is_missing() -> None:
    fake_client = _FakeClient()
    password = TEST_ACCOUNT_PASSWORD

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda _username: False,
        load_keystore_fn=lambda *_args, **_kwargs: object(),
        derive_storage_key_fn=lambda *_args, **_kwargs: None,
        init_store_fn=lambda *_args, **_kwargs: _async_noop(),
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: {},
    )

    result = await execute_login_handshake(
        context,
        username="alice",
        password=password,
        totp_code="123456",
    )

    assert result == LoginFailed(message="No local keys found. Register first.")
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_execute_login_handshake_closes_client_when_keystore_load_fails() -> None:
    fake_client = _FakeClient()
    password = TEST_ACCOUNT_PASSWORD

    def _raise_value_error(*_args, **_kwargs):
        raise ValueError("bad keystore")

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda _username: True,
        load_keystore_fn=_raise_value_error,
        derive_storage_key_fn=lambda *_args, **_kwargs: None,
        init_store_fn=lambda *_args, **_kwargs: _async_noop(),
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: {},
    )

    result = await execute_login_handshake(
        context,
        username="alice",
        password=password,
        totp_code="123456",
    )

    assert result == LoginFailed(message="Wrong password or corrupted keystore.")
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_execute_login_handshake_waits_for_initial_websocket_startup_outcome() -> None:
    fake_client = _FakeClient()
    password = TEST_ACCOUNT_PASSWORD
    ready = asyncio.Event()

    async def _connect_ws(_on_message) -> None:
        await ready.wait()
        fake_client.connected = True

    fake_client.connect_ws = cast(Any, _connect_ws)

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda _username: True,
        load_keystore_fn=lambda *_args, **_kwargs: object(),
        derive_storage_key_fn=lambda *_args, **_kwargs: None,
        init_store_fn=lambda *_args, **_kwargs: _async_noop(),
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: {},
    )

    task = asyncio.create_task(
        execute_login_handshake(
            context,
            username="alice",
            password=password,
            totp_code="123456",
        )
    )
    await asyncio.sleep(0)

    assert task.done() is False

    ready.set()
    result = await task

    assert isinstance(result, LoginSucceeded)
    assert result.client is fake_client
    assert fake_client.connected is True


@pytest.mark.asyncio
async def test_execute_login_handshake_fails_closed_when_canonical_user_lookup_fails() -> None:
    fake_client = _FakeClient()
    password = TEST_ACCOUNT_PASSWORD

    async def _get_keys(_username: str) -> Any:
        raise _imclient_error(404, "User not found")

    fake_client.get_keys = cast(Any, _get_keys)

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda _username: True,
        load_keystore_fn=lambda *_args, **_kwargs: object(),
        derive_storage_key_fn=lambda *_args, **_kwargs: None,
        init_store_fn=lambda *_args, **_kwargs: _async_noop(),
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: {},
    )

    result = await execute_login_handshake(
        context,
        username="alice",
        password=password,
        totp_code="123456",
    )

    assert result == LoginFailed(message="Login failed: User not found")
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_execute_login_handshake_fails_closed_when_local_store_bootstrap_fails() -> None:
    fake_client = _FakeClient()
    password = TEST_ACCOUNT_PASSWORD

    async def _init_store(_username: str) -> None:
        raise RuntimeError("storage init exploded")

    context = LoginContext(
        server_url="https://example.test",
        verify_tls=True,
        ca_cert=None,
        pin_sha256=None,
        on_ws_message=_on_ws_message,
        client_factory=lambda **_kwargs: _client_factory(fake_client),
        keystore_exists_fn=lambda _username: True,
        load_keystore_fn=lambda *_args, **_kwargs: object(),
        derive_storage_key_fn=lambda *_args, **_kwargs: None,
        init_store_fn=_init_store,
        sweep_expired_fn=lambda: _async_noop(),
        load_sessions_fn=lambda *_args, **_kwargs: {},
    )

    result = await execute_login_handshake(
        context,
        username="alice",
        password=password,
        totp_code="123456",
    )

    assert result == LoginFailed(message="Login failed: Local storage unavailable.")
    assert fake_client.closed is True


async def _async_noop(*_args, **_kwargs) -> None:
    return None
