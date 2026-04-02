from __future__ import annotations

import base64
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from client.api.client import IMClient, IMClientError
from client.crypto.session import (
    PEER_BUNDLE_BLOCKED_MESSAGE,
    IdentityKeyCache,
    InvalidPeerBundleError,
    LocalStorageSecurityError,
    ReplayProtector,
    SecurityError,
    build_and_encrypt,
    check_and_update_verified_peer_bundle,
    derive_ratchet_chains,
    derive_session_key_as_initiator,
    validate_and_decode_peer_bundle,
)
from client.crypto.storage import LocalKeys, SessionState
from client.state.store import save_message, upsert_conversation
from client.use_cases.results import LocalSecurityFailure, NetworkFailure, ServerFailure


@dataclass
class SendMessageContext:
    client: IMClient | Any | None
    local_keys: LocalKeys | Any | None
    username: str
    user_id: str | None
    sessions: dict[str, SessionState]
    peer_usernames: dict[str, str]
    ttl_settings: dict[str, int | None]
    counters: dict[str, int]
    persist_sessions: Callable[[], None]


@dataclass(frozen=True)
class SendMessageSucceeded:
    sent_at: int


@dataclass(frozen=True)
class SendMessageBlocked:
    reason: LocalSecurityFailure
    sent_at: int | None = None
    disable_send: bool = False


type SendMessageResult = SendMessageSucceeded | SendMessageBlocked | NetworkFailure | ServerFailure


async def ensure_session(
    context: SendMessageContext,
    conv_id: str,
    peer_id: str,
) -> SessionState | None:
    if conv_id in context.sessions:
        return context.sessions[conv_id]

    if context.local_keys is None or context.client is None:
        return None

    peer_username = context.peer_usernames.get(peer_id)
    if peer_username is None:
        return None

    peer_bundle = validate_and_decode_peer_bundle(await context.client.get_keys(peer_username))

    my_id = context.user_id or context.username

    session_key, eph_pub_bytes, conv_dh_pub_bytes = derive_session_key_as_initiator(
        my_identity_kp=context.local_keys.identity_kp,
        my_dh_kp=context.local_keys.dh_kp,
        peer_identity_pub_bytes=peer_bundle.identity_pub,
        peer_dh_pub_bytes=peer_bundle.dh_pub,
        my_user_id=my_id,
        peer_user_id=peer_id,
        conversation_id=conv_id,
    )

    send_chain, recv_chain = derive_ratchet_chains(session_key.raw, initiator=True)
    cache = IdentityKeyCache()
    check_and_update_verified_peer_bundle(cache, peer_id, peer_bundle)

    state = SessionState(
        session_key=session_key,
        send_chain=send_chain,
        recv_chain=recv_chain,
        replay_protector=ReplayProtector(),
        identity_key_cache=cache,
        next_outbound_counter=0,
    )
    state._eph_pub_b64 = base64.b64encode(eph_pub_bytes).decode()  # type: ignore[attr-defined]
    state._conv_dh_pub_b64 = base64.b64encode(conv_dh_pub_bytes).decode()  # type: ignore[attr-defined]

    context.sessions[conv_id] = state
    context.persist_sessions()
    return state


async def execute_send_message(
    context: SendMessageContext,
    *,
    conversation_id: str,
    peer_id: str,
    plaintext: str,
    now: Callable[[], int] | Callable[[], float] = time.time,
    ensure_session_fn: Callable[
        [SendMessageContext, str, str], Awaitable[SessionState | None]
    ] = ensure_session,
    build_and_encrypt_fn: Callable[..., Any] = build_and_encrypt,
    save_message_fn: Callable[..., Awaitable[None]] = save_message,
    upsert_conversation_fn: Callable[..., Awaitable[None]] = upsert_conversation,
) -> SendMessageResult:
    if context.client is None:
        return ServerFailure(message="The message could not be sent.")

    ttl = context.ttl_settings.get(conversation_id)
    my_id = context.user_id or context.username

    try:
        session_state = await ensure_session_fn(context, conversation_id, peer_id)
    except InvalidPeerBundleError:
        return SendMessageBlocked(
            reason=LocalSecurityFailure(message=PEER_BUNDLE_BLOCKED_MESSAGE),
            sent_at=None,
            disable_send=True,
        )
    except IMClientError as exc:
        return ServerFailure(message=exc.detail)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return NetworkFailure()

    if session_state is None:
        return ServerFailure(message="The message could not be sent.")

    restored_next_counter = getattr(session_state, "next_outbound_counter", 0)
    counter = context.counters.get(conversation_id, restored_next_counter)

    sent_at = int(now())
    envelope = build_and_encrypt_fn(
        send_chain=session_state.send_chain,
        plaintext=plaintext,
        sender_id=my_id,
        recipient_id=peer_id,
        conversation_id=conversation_id,
        counter=counter,
        ttl_seconds=ttl,
        sent_at=sent_at,
    )
    if counter == 0 and hasattr(session_state, "_eph_pub_b64"):
        envelope.eph_pub_b64 = session_state._eph_pub_b64  # type: ignore[attr-defined]
    if counter == 0 and hasattr(session_state, "_conv_dh_pub_b64"):
        envelope.conv_dh_pub_b64 = session_state._conv_dh_pub_b64  # type: ignore[attr-defined]

    try:
        await context.client.send_message(envelope)
        await save_message_fn(
            id=envelope.id,
            conversation_id=conversation_id,
            sender_id=my_id,
            recipient_id=peer_id,
            counter=counter,
            plaintext=plaintext,
            sent_at=sent_at,
            ttl_seconds=ttl,
            delivery_status="sent",
        )
    except (LocalStorageSecurityError, SecurityError):
        return SendMessageBlocked(
            reason=LocalSecurityFailure(message="Local secure storage is unavailable."),
            sent_at=sent_at,
            disable_send=True,
        )

    next_outbound_counter = counter + 1
    context.counters[conversation_id] = next_outbound_counter
    session_state.next_outbound_counter = next_outbound_counter
    context.persist_sessions()
    await upsert_conversation_fn(
        id=conversation_id,
        peer_id=peer_id,
        peer_username=context.peer_usernames.get(peer_id, peer_id),
        last_message_at=sent_at,
        unread_count=0,
    )
    return SendMessageSucceeded(sent_at=sent_at)
