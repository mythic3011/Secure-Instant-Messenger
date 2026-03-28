from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from client.api.client import IMClientConnectionError, IMClientError
from client.use_cases.bootstrap import (
    BootstrapContext,
    BootstrapFailed,
    run_post_login_bootstrap,
)
from shared.protocol import LoginRequest


class LoginClientProtocol(Protocol):
    async def __aenter__(self) -> object: ...
    async def __aexit__(self, *_args: object) -> None: ...
    async def login(self, body: LoginRequest) -> object: ...
    async def get_keys(self, username: str) -> Any: ...
    async def connect_ws(self, on_message: Callable[[dict], Awaitable[None]]) -> None: ...


@dataclass
class LoginContext:
    server_url: str
    verify_tls: bool
    ca_cert: str | None
    pin_sha256: str | None
    on_ws_message: Callable[[dict], Awaitable[None]]
    client_factory: Callable[..., LoginClientProtocol]
    keystore_exists_fn: Callable[[str], bool]
    load_keystore_fn: Callable[[str, str], Any]
    derive_storage_key_fn: Callable[[str, str], None]
    init_store_fn: Callable[[str], Awaitable[None]]
    sweep_expired_fn: Callable[[], Awaitable[object]]
    load_sessions_fn: Callable[[str, str], dict[str, Any]]


@dataclass(frozen=True)
class LoginSucceeded:
    client: LoginClientProtocol
    username: str
    password: str
    user_id: str
    local_keys: Any
    sessions: dict[str, Any]


@dataclass(frozen=True)
class LoginFailed:
    message: str


type LoginHandshakeResult = LoginSucceeded | LoginFailed


async def _close_and_fail(client: LoginClientProtocol, message: str) -> LoginFailed:
    await client.__aexit__(None, None, None)
    return LoginFailed(message=message)


async def execute_login_handshake(
    context: LoginContext,
    *,
    username: str,
    password: str,
    totp_code: str,
) -> LoginHandshakeResult:
    client = context.client_factory(
        base_url=context.server_url,
        verify_tls=context.verify_tls,
        ca_cert=context.ca_cert,
        pin_sha256=context.pin_sha256,
    )
    await client.__aenter__()

    try:
        await client.login(LoginRequest(username=username, password=password, totp_code=totp_code))
    except IMClientError as exc:
        return await _close_and_fail(client, f"Login failed: {exc.detail}")
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        return await _close_and_fail(
            client,
            f"Cannot reach server — is it running? ({type(exc).__name__})",
        )
    except Exception as exc:
        return await _close_and_fail(client, f"Unexpected error: {exc}")

    if not context.keystore_exists_fn(username):
        return await _close_and_fail(client, "No local keys found. Register first.")

    try:
        local_keys = context.load_keystore_fn(username, password)
        context.derive_storage_key_fn(username, password)
    except ValueError:
        return await _close_and_fail(client, "Wrong password or corrupted keystore.")

    bootstrap = await run_post_login_bootstrap(
        BootstrapContext(
            init_store_fn=context.init_store_fn,
            sweep_expired_fn=context.sweep_expired_fn,
            load_sessions_fn=context.load_sessions_fn,
        ),
        client=client,
        username=username,
        password=password,
    )
    if isinstance(bootstrap, BootstrapFailed):
        return await _close_and_fail(client, bootstrap.message)

    try:
        await client.connect_ws(context.on_ws_message)
    except IMClientConnectionError as exc:
        return await _close_and_fail(client, f"Login failed: {exc.detail}")
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return await _close_and_fail(client, "Login failed: Cannot reach server.")

    return LoginSucceeded(
        client=client,
        username=username,
        password=password,
        user_id=bootstrap.user_id,
        local_keys=local_keys,
        sessions=bootstrap.sessions,
    )
