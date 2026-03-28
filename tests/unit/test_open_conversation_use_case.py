from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from client.api.client import IMClientError
from client.use_cases.open_conversation import (
    OpenConversationContext,
    execute_open_conversation,
)
from client.use_cases.results import NetworkFailure, ServerFailure, Success


@pytest.mark.asyncio
async def test_execute_open_conversation_marks_remote_then_clears_local() -> None:
    calls: list[str] = []

    async def _mark_read(conversation_id: str) -> None:
        calls.append(f"remote:{conversation_id}")

    async def _reset_unread(conversation_id: str) -> None:
        calls.append(f"local:{conversation_id}")

    result = await execute_open_conversation(
        OpenConversationContext(
            client=SimpleNamespace(mark_read=_mark_read),
            reset_unread_fn=_reset_unread,
        ),
        conversation_id="conv-1",
    )

    assert result == Success()
    assert calls == ["remote:conv-1", "local:conv-1"]


@pytest.mark.asyncio
async def test_execute_open_conversation_keeps_local_unread_when_remote_mark_read_fails() -> None:
    calls: list[str] = []

    async def _mark_read(_conversation_id: str) -> None:
        raise httpx.ConnectError("offline")

    async def _reset_unread(conversation_id: str) -> None:
        calls.append(f"local:{conversation_id}")

    result = await execute_open_conversation(
        OpenConversationContext(
            client=SimpleNamespace(mark_read=_mark_read),
            reset_unread_fn=_reset_unread,
        ),
        conversation_id="conv-1",
    )

    assert result == NetworkFailure()
    assert calls == []


@pytest.mark.asyncio
async def test_execute_open_conversation_surfaces_server_failure() -> None:
    async def _mark_read(_conversation_id: str) -> None:
        raise IMClientError(403, '{"detail":"Not part of this conversation"}')

    async def _reset_unread(_conversation_id: str) -> None:
        return None

    result = await execute_open_conversation(
        OpenConversationContext(
            client=SimpleNamespace(mark_read=_mark_read),
            reset_unread_fn=_reset_unread,
        ),
        conversation_id="conv-1",
    )

    assert result == ServerFailure(message="Not part of this conversation")
