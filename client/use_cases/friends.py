from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from client.api.client import IMClientError
from client.use_cases.results import NetworkFailure, ServerFailure

FriendAction = Literal["accept", "decline"]


@dataclass
class FriendsContext:
    client: Any | None


@dataclass(frozen=True)
class PendingRequestsLoaded:
    requests: list[dict[str, Any]]


@dataclass(frozen=True)
class FriendRequestHandled:
    requests: list[dict[str, Any]]
    status_message: str


type LoadPendingResult = PendingRequestsLoaded | NetworkFailure | ServerFailure
type HandleFriendRequestResult = FriendRequestHandled | NetworkFailure | ServerFailure


async def execute_load_pending_requests(context: FriendsContext) -> LoadPendingResult:
    if context.client is None:
        return ServerFailure(message="Pending requests are unavailable.")
    try:
        requests = await context.client.list_pending_requests()
    except IMClientError as exc:
        return ServerFailure(message=exc.detail)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return NetworkFailure(message="Cannot reach server. Pending requests unavailable.")
    return PendingRequestsLoaded(requests=[request.model_dump() for request in requests])


async def execute_handle_friend_request(
    context: FriendsContext,
    *,
    request_id: str,
    action: FriendAction,
) -> HandleFriendRequestResult:
    if context.client is None:
        return ServerFailure(message="Pending request could not be updated.")
    try:
        await context.client.handle_friend_request(request_id, action)
        requests = await context.client.list_pending_requests()
    except IMClientError as exc:
        return ServerFailure(message=exc.detail)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return NetworkFailure(message="Cannot reach server. Pending request unchanged.")
    verb = "accepted" if action == "accept" else "declined"
    return FriendRequestHandled(
        requests=[request.model_dump() for request in requests],
        status_message=f"Friend request {verb}.",
    )
