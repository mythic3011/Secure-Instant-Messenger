from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from client.api.client import IMClientError
from client.use_cases.friends import (
    FriendRequestHandled,
    FriendsContext,
    PendingRequestsLoaded,
    execute_handle_friend_request,
    execute_load_pending_requests,
)
from client.use_cases.results import NetworkFailure, ServerFailure


def _imclient_error(status_code: int, detail: str) -> IMClientError:
    return IMClientError(status_code, f'{{"detail":"{detail}"}}')


@pytest.mark.asyncio
async def test_execute_load_pending_requests_returns_payloads() -> None:
    async def _list_pending_requests():
        return [
            SimpleNamespace(model_dump=lambda: {"id": "req-1", "sender_name": "bob"}),
            SimpleNamespace(model_dump=lambda: {"id": "req-2", "sender_name": "charlie"}),
        ]

    context = FriendsContext(client=SimpleNamespace(list_pending_requests=_list_pending_requests))

    result = await execute_load_pending_requests(context)

    assert result == PendingRequestsLoaded(
        requests=[
            {"id": "req-1", "sender_name": "bob"},
            {"id": "req-2", "sender_name": "charlie"},
        ]
    )


@pytest.mark.asyncio
async def test_execute_load_pending_requests_returns_server_failure() -> None:
    async def _raise_pending():
        raise _imclient_error(409, "Still pending")

    context = FriendsContext(client=SimpleNamespace(list_pending_requests=_raise_pending))

    result = await execute_load_pending_requests(context)

    assert result == ServerFailure(message="Still pending")


@pytest.mark.asyncio
async def test_execute_handle_friend_request_returns_success_with_refreshed_payloads() -> None:
    calls: list[tuple[str, str]] = []

    async def _handle_friend_request(request_id: str, action: str) -> None:
        calls.append((request_id, action))

    async def _list_pending_requests():
        return [SimpleNamespace(model_dump=lambda: {"id": "req-2", "sender_name": "charlie"})]

    context = FriendsContext(
        client=SimpleNamespace(
            handle_friend_request=_handle_friend_request,
            list_pending_requests=_list_pending_requests,
        )
    )

    result = await execute_handle_friend_request(context, request_id="req-1", action="accept")

    assert calls == [("req-1", "accept")]
    assert result == FriendRequestHandled(
        requests=[{"id": "req-2", "sender_name": "charlie"}],
        status_message="Friend request accepted.",
    )


@pytest.mark.asyncio
async def test_execute_handle_friend_request_returns_network_failure() -> None:
    async def _raise_handle(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    context = FriendsContext(
        client=SimpleNamespace(
            handle_friend_request=_raise_handle,
            list_pending_requests=None,
        )
    )

    result = await execute_handle_friend_request(context, request_id="req-1", action="decline")

    assert result == NetworkFailure(message="Cannot reach server. Pending request unchanged.")
