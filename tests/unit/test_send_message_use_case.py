from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from client.api.client import IMClientError
from client.crypto.session import LocalStorageSecurityError
from client.crypto.storage import SessionState
from client.use_cases.results import LocalSecurityFailure, NetworkFailure, ServerFailure
from client.use_cases.send_message import (
    SendMessageBlocked,
    SendMessageContext,
    SendMessageSucceeded,
    execute_send_message,
)


def _imclient_error(status_code: int, detail: str) -> IMClientError:
    return IMClientError(status_code, f'{{"detail":"{detail}"}}')


def _session_state() -> SessionState:
    return cast(SessionState, SimpleNamespace(send_chain=object(), next_outbound_counter=0))


@pytest.mark.asyncio
async def test_execute_send_message_returns_success_and_updates_state() -> None:
    sent_envelopes: list[object] = []
    saved_messages: list[dict[str, object]] = []
    upserted_conversations: list[dict[str, object]] = []
    persisted = False
    session_state = _session_state()

    async def _send_message(envelope: object) -> None:
        sent_envelopes.append(envelope)

    async def _ensure_session(*_args, **_kwargs):
        return session_state

    async def _save_message(**kwargs) -> None:
        saved_messages.append(kwargs)

    async def _upsert_conversation(**kwargs) -> None:
        upserted_conversations.append(kwargs)

    def _persist_sessions() -> None:
        nonlocal persisted
        persisted = True

    context = SendMessageContext(
        client=SimpleNamespace(send_message=_send_message),
        local_keys=SimpleNamespace(),
        username="alice",
        user_id="alice-id",
        sessions={},
        peer_usernames={"bob-id": "bob"},
        ttl_settings={"conv-1": 60},
        counters={"conv-1": 0},
        persist_sessions=_persist_sessions,
    )

    result = await execute_send_message(
        context,
        conversation_id="conv-1",
        peer_id="bob-id",
        plaintext="hello",
        now=lambda: 123,
        ensure_session_fn=_ensure_session,
        build_and_encrypt_fn=lambda **_kwargs: SimpleNamespace(
            id="msg-1",
            eph_pub_b64=None,
            conv_dh_pub_b64=None,
        ),
        save_message_fn=_save_message,
        upsert_conversation_fn=_upsert_conversation,
    )

    assert result == SendMessageSucceeded(sent_at=123)
    assert len(sent_envelopes) == 1
    assert saved_messages[0]["delivery_status"] == "sent"
    assert upserted_conversations[0]["peer_username"] == "bob"
    assert context.counters["conv-1"] == 1
    assert session_state.next_outbound_counter == 1
    assert persisted is True


@pytest.mark.asyncio
async def test_execute_send_message_returns_server_failure_for_session_error() -> None:
    context = SendMessageContext(
        client=SimpleNamespace(send_message=None),
        local_keys=SimpleNamespace(),
        username="alice",
        user_id="alice-id",
        sessions={},
        peer_usernames={"bob-id": "bob"},
        ttl_settings={},
        counters={},
        persist_sessions=lambda: None,
    )

    async def _raise_session(*_args, **_kwargs):
        raise _imclient_error(404, "Peer key unavailable")

    result = await execute_send_message(
        context,
        conversation_id="conv-1",
        peer_id="bob-id",
        plaintext="hello",
        ensure_session_fn=_raise_session,
    )

    assert result == ServerFailure(message="Peer key unavailable")


@pytest.mark.asyncio
async def test_execute_send_message_returns_network_failure_for_session_error() -> None:
    context = SendMessageContext(
        client=SimpleNamespace(send_message=None),
        local_keys=SimpleNamespace(),
        username="alice",
        user_id="alice-id",
        sessions={},
        peer_usernames={"bob-id": "bob"},
        ttl_settings={},
        counters={},
        persist_sessions=lambda: None,
    )

    async def _raise_session(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    result = await execute_send_message(
        context,
        conversation_id="conv-1",
        peer_id="bob-id",
        plaintext="hello",
        ensure_session_fn=_raise_session,
    )

    assert result == NetworkFailure()


@pytest.mark.asyncio
async def test_execute_send_message_returns_local_security_block_with_sent_at() -> None:
    persisted = False
    session_state = _session_state()

    async def _send_message(_envelope: object) -> None:
        return None

    async def _ensure_session(*_args, **_kwargs):
        return session_state

    async def _raise_save_message(**_kwargs) -> None:
        raise LocalStorageSecurityError("no storage key")

    def _persist_sessions() -> None:
        nonlocal persisted
        persisted = True

    context = SendMessageContext(
        client=SimpleNamespace(send_message=_send_message),
        local_keys=SimpleNamespace(),
        username="alice",
        user_id="alice-id",
        sessions={},
        peer_usernames={"bob-id": "bob"},
        ttl_settings={},
        counters={},
        persist_sessions=_persist_sessions,
    )

    result = await execute_send_message(
        context,
        conversation_id="conv-1",
        peer_id="bob-id",
        plaintext="hello",
        now=lambda: 456,
        ensure_session_fn=_ensure_session,
        build_and_encrypt_fn=lambda **_kwargs: SimpleNamespace(
            id="msg-1",
            eph_pub_b64=None,
            conv_dh_pub_b64=None,
        ),
        save_message_fn=_raise_save_message,
    )

    assert result == SendMessageBlocked(
        reason=LocalSecurityFailure(message="Local secure storage is unavailable."),
        sent_at=456,
        disable_send=True,
    )
    assert session_state.next_outbound_counter == 0
    assert context.counters == {}
    assert persisted is False


@pytest.mark.asyncio
async def test_execute_send_message_uses_restored_next_outbound_counter() -> None:
    persisted = False
    session_state = cast(
        SessionState, SimpleNamespace(send_chain=object(), next_outbound_counter=3)
    )
    built_counters: list[int] = []

    async def _send_message(_envelope: object) -> None:
        return None

    async def _ensure_session(*_args, **_kwargs):
        return session_state

    async def _save_message(**_kwargs) -> None:
        return None

    async def _upsert_conversation(**_kwargs) -> None:
        return None

    def _persist_sessions() -> None:
        nonlocal persisted
        persisted = True

    context = SendMessageContext(
        client=SimpleNamespace(send_message=_send_message),
        local_keys=SimpleNamespace(),
        username="alice",
        user_id="alice-id",
        sessions={},
        peer_usernames={"bob-id": "bob"},
        ttl_settings={},
        counters={},
        persist_sessions=_persist_sessions,
    )

    result = await execute_send_message(
        context,
        conversation_id="conv-1",
        peer_id="bob-id",
        plaintext="hello",
        now=lambda: 789,
        ensure_session_fn=_ensure_session,
        build_and_encrypt_fn=lambda **kwargs: (
            built_counters.append(kwargs["counter"])
            or SimpleNamespace(
                id="msg-1",
                eph_pub_b64=None,
                conv_dh_pub_b64=None,
            )
        ),
        save_message_fn=_save_message,
        upsert_conversation_fn=_upsert_conversation,
    )

    assert result == SendMessageSucceeded(sent_at=789)
    assert built_counters == [3]
    assert context.counters["conv-1"] == 4
    assert session_state.next_outbound_counter == 4
    assert persisted is True
