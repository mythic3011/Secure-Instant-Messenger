from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from client.api.client import IMClientError


class BootstrapClientProtocol(Protocol):
    async def get_keys(self, username: str) -> Any: ...


@dataclass(frozen=True)
class BootstrapSucceeded:
    user_id: str
    sessions: dict[str, Any]


@dataclass(frozen=True)
class BootstrapFailed:
    message: str


type BootstrapResult = BootstrapSucceeded | BootstrapFailed


@dataclass
class BootstrapContext:
    init_store_fn: Callable[[str], Awaitable[None]]
    sweep_expired_fn: Callable[[], Awaitable[object]]
    load_sessions_fn: Callable[[str, str], dict[str, Any]]


async def run_post_login_bootstrap(
    context: BootstrapContext,
    *,
    client: BootstrapClientProtocol,
    username: str,
    password: str,
) -> BootstrapResult:
    try:
        bundle = await client.get_keys(username)
        user_id = bundle.user_id
    except IMClientError as exc:
        return BootstrapFailed(message=f"Login failed: {exc.detail}")
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return BootstrapFailed(message="Login failed: Cannot reach server.")

    try:
        await context.init_store_fn(username)
        await context.sweep_expired_fn()
        sessions = context.load_sessions_fn(username, password)
    except Exception:
        return BootstrapFailed(message="Login failed: Local storage unavailable.")

    return BootstrapSucceeded(user_id=user_id, sessions=sessions)
