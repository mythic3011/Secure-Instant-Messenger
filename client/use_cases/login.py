from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from client.api.client import IMClient, IMClientConnectionError, IMClientError
from shared.protocol import LoginRequest


@dataclass
class LoginContext:
    server_url: str
    verify_tls: bool
    ca_cert: str | None
    pin_sha256: str | None
    on_ws_message: Callable[[dict], Awaitable[None]]
    client_factory: Callable[..., IMClient | Any]
    keystore_exists_fn: Callable[[str], bool]
    load_keystore_fn: Callable[[str, str], Any]
    derive_storage_key_fn: Callable[[str, str], None]
    init_store_fn: Callable[[str], Awaitable[None]]
    sweep_expired_fn: Callable[[], Awaitable[None]]
    load_sessions_fn: Callable[[str, str], dict[str, Any]]


@dataclass(frozen=True)
class LoginSucceeded:
    client: IMClient | Any
    username: str
    password: str
    user_id: str
    local_keys: Any
    sessions: dict[str, Any]


@dataclass(frozen=True)
class LoginFailed:
    message: str


type LoginHandshakeResult = LoginSucceeded | LoginFailed


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
        await client.__aexit__(None, None, None)
        return LoginFailed(message=f"Login failed: {exc.detail}")
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        await client.__aexit__(None, None, None)
        return LoginFailed(message=f"Cannot reach server — is it running? ({type(exc).__name__})")
    except Exception as exc:
        await client.__aexit__(None, None, None)
        return LoginFailed(message=f"Unexpected error: {exc}")

    if not context.keystore_exists_fn(username):
        return LoginFailed(message="No local keys found. Register first.")

    try:
        local_keys = context.load_keystore_fn(username, password)
        context.derive_storage_key_fn(username, password)
    except ValueError:
        return LoginFailed(message="Wrong password or corrupted keystore.")

    await context.init_store_fn(username)
    await context.sweep_expired_fn()
    sessions = context.load_sessions_fn(username, password)

    try:
        bundle = await client.get_keys(username)
        user_id = bundle.user_id
    except (IMClientError, httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        user_id = username

    try:
        await client.connect_ws(context.on_ws_message)
    except IMClientConnectionError as exc:
        await client.__aexit__(None, None, None)
        return LoginFailed(message=f"Login failed: {exc.detail}")
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        await client.__aexit__(None, None, None)
        return LoginFailed(message="Login failed: Cannot reach server.")

    return LoginSucceeded(
        client=client,
        username=username,
        password=password,
        user_id=user_id,
        local_keys=local_keys,
        sessions=sessions,
    )
