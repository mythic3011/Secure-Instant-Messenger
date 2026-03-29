from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from client.api.client import IMClientError
from client.use_cases.results import NetworkFailure, ServerFailure, Success


@dataclass
class OpenConversationContext:
    client: Any | None
    reset_unread_fn: Callable[[str], Awaitable[None]]


type OpenConversationResult = Success | NetworkFailure | ServerFailure


async def execute_open_conversation(
    context: OpenConversationContext,
    *,
    conversation_id: str,
) -> OpenConversationResult:
    if context.client is None:
        return Success()

    try:
        await context.client.mark_read(conversation_id)
    except IMClientError as exc:
        return ServerFailure(message=exc.detail)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return NetworkFailure()

    await context.reset_unread_fn(conversation_id)
    return Success()
