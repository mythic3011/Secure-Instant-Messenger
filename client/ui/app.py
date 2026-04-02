"""
client/ui/app.py — Main Textual TUI application.

Wires together all screens, IMClient, crypto, and local storage.
Handles all message events from screens and dispatches WebSocket pushes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections import OrderedDict
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import httpx
import structlog
from pydantic import ValidationError
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Static

from client.api.client import IMClient, IMClientError
from client.crypto.session import (
    PEER_BUNDLE_BLOCKED_MESSAGE,
    DHKeypair,
    IdentityKeyCache,
    IdentityKeypair,
    IntegrityError,
    InvalidPeerBundleError,
    KeyChangeWarning,
    LocalStorageSecurityError,
    ReplayProtector,
    SecurityError,
    build_and_encrypt,
    check_and_update_verified_peer_bundle,
    compute_fingerprint,
    decrypt_envelope,
    derive_ratchet_chains,
    derive_session_key_as_responder,
    make_key_signature,
    validate_and_decode_peer_bundle,
)
from client.crypto.storage import (
    LocalKeys,
    SessionState,
    _derive_storage_key,
    keystore_exists,
    load_keystore,
    load_sessions,
    save_keystore,
    save_sessions,
)
from client.state.store import (
    get_conversations,
    increment_unread,
    init_store,
    reset_unread,
    save_message,
    set_storage_key,
    sweep_expired,
    update_delivery_status,
    upsert_conversation,
)
from client.ui.contracts import (
    ChatHistoryResult,
    ConversationSummaryViewModel,
    SendResult,
    TrustViewModel,
    UIBanner,
    UIScreenState,
)
from client.ui.screens.conversations import ConversationListScreen
from client.ui.screens.login import LoginScreen
from client.use_cases import (
    FriendRequestHandled,
    FriendRequestSent,
    FriendsContext,
    LoginContext,
    LoginFailed,
    LoginSucceeded,
    NetworkFailure,
    PendingRequestsLoaded,
    SendFriendRequestResult,
    SendMessageBlocked,
    SendMessageContext,
    SendMessageSucceeded,
    ServerFailure,
    Success,
    execute_handle_friend_request,
    execute_load_pending_requests,
    execute_login_handshake,
    execute_send_friend_request,
    execute_send_message,
)
from client.use_cases import (
    ensure_session as ensure_send_session,
)
from client.use_cases.open_conversation import (
    OpenConversationContext,
    execute_open_conversation,
)
from shared.protocol import (
    DeliveryAck,
    FriendRequestPushPayload,
    FriendRequestStatus,
    MessageEnvelope,
    RegisterRequest,
)

log = structlog.get_logger()

LOCAL_HISTORY_UNAVAILABLE = UIBanner(
    code="local_history_unavailable",
    severity="warning",
    title="Local history unavailable",
    message="Local chat history is unavailable on this device.",
)
LOCAL_STORAGE_UNAVAILABLE = UIBanner(
    code="local_storage_unavailable",
    severity="error",
    title="Secure storage unavailable",
    message="Local secure storage is unavailable. Message was not sent.",
    persistent=True,
    dismissible=False,
)
KEY_CHANGED_BANNER = UIBanner(
    code="key_changed",
    severity="error",
    title="Identity key changed",
    message=(
        "This contact's identity key changed. Verify the fingerprint before trusting new messages."
    ),
    persistent=True,
    dismissible=False,
)
TRUST_UNVERIFIED_BANNER = UIBanner(
    code="trust_unverified",
    severity="warning",
    title="Contact not verified",
    message=(
        "This contact is not verified yet. Compare the fingerprint "
        "before trusting sensitive messages."
    ),
    persistent=True,
)
NETWORK_UNAVAILABLE_BANNER = UIBanner(
    code="network_unavailable",
    severity="warning",
    title="Network unavailable",
    message="The server is unreachable. Retry when the connection recovers.",
)
MESSAGE_SEND_FAILED_BANNER = UIBanner(
    code="message_send_failed",
    severity="error",
    title="Send failed",
    message="The message could not be sent.",
)
_INVALID_BUNDLE_LOG_KEY_LIMIT = 256
_INVALID_BUNDLE_HASH_FIELD_CHARS = 128
PEER_BUNDLE_BLOCKED_BANNER = UIBanner(
    code="message_send_blocked",
    severity="error",
    title="Session setup blocked",
    message=PEER_BUNDLE_BLOCKED_MESSAGE,
    persistent=True,
    dismissible=False,
)


def _clean_validation_error_message(message: str) -> str:
    return message.removeprefix("Value error, ")


@runtime_checkable
class FriendsScreenActions(Protocol):
    def populate_pending(self, requests: list[dict[str, Any]]) -> None: ...
    def show_status(self, msg: str) -> None: ...
    def show_error(self, msg: str) -> None: ...


class IMApp(App):
    """
    Main application. Lifecycle:
      1. Show LoginScreen
      2. On login: load keystore, connect WS, show ConversationListScreen
      3. On conversation select: push ChatScreen
    """

    TITLE = "COMP3334 Secure IM"

    def __init__(
        self,
        server_url: str,
        username: str,
        verify_tls: bool = True,
        ca_cert: str | None = None,
        pin_sha256: str | None = None,
    ) -> None:
        super().__init__()
        self._server_url = server_url
        self._username = username
        self._verify_tls = verify_tls
        self._ca_cert = ca_cert
        self._pin_sha256 = pin_sha256
        self._client: IMClient | None = None
        self._local_keys: LocalKeys | None = None
        self._password: str | None = None
        self._user_id: str | None = None
        # Per-conversation state: conv_id -> SessionState
        self._sessions: dict[str, SessionState] = {}
        # Per-conversation TTL setting
        self._ttl_settings: dict[str, int | None] = {}
        # Per-conversation send counter
        self._counters: dict[str, int] = {}
        # peer_id -> username cache (for get_keys lookups)
        self._peer_usernames: dict[str, str] = {}
        self._invalid_peer_bundle_log_keys: OrderedDict[str, None] = OrderedDict()

    def _persist_sessions(self) -> None:
        if self._local_keys and self._password and self._username:
            save_sessions(self._username, self._password, self._sessions)

    @staticmethod
    def _rehydrate_outbound_counters(sessions: dict[str, SessionState]) -> dict[str, int]:
        return {
            conversation_id: state.next_outbound_counter
            for conversation_id, state in sessions.items()
        }

    @staticmethod
    def _render_register_error(screen: Any, message: str) -> None:
        try:
            screen.query_one("#error", Static).update(message)
        except NoMatches as exc:
            log.warning("register_error_render_failed", err=str(exc))

    def _derive_and_set_storage_key(self, username: str, password: str) -> None:
        keystore_path = Path.home() / ".comp3334im" / username / "keystore.json"
        import json

        keystore_data = json.loads(keystore_path.read_text())
        salt = base64.b64decode(keystore_data["argon2_salt_b64"])
        storage_key = _derive_storage_key(password, salt)
        set_storage_key(storage_key)

    @staticmethod
    def _banner_priority(code: str) -> int:
        priorities = {
            "key_changed": 0,
            "local_storage_unavailable": 1,
            "ciphertext_tampered": 2,
            "network_unavailable": 3,
            "trust_unverified": 4,
            "message_send_failed": 5,
            "server_error": 6,
            "local_history_unavailable": 7,
            "message_send_blocked": 8,
            "replay_rejected": 9,
        }
        return priorities.get(code, 99)

    @staticmethod
    def _server_error_banner(message: str) -> UIBanner:
        return UIBanner(
            code="server_error",
            severity="error",
            title="Server error",
            message=message,
        )

    @staticmethod
    def _local_security_banner(message: str) -> UIBanner:
        if message in {
            LOCAL_STORAGE_UNAVAILABLE.message,
            "Local secure storage is unavailable.",
        }:
            return LOCAL_STORAGE_UNAVAILABLE
        if message == PEER_BUNDLE_BLOCKED_MESSAGE:
            return PEER_BUNDLE_BLOCKED_BANNER
        return UIBanner(
            code="message_send_blocked",
            severity="error",
            title="Message blocked",
            message=message,
            persistent=True,
            dismissible=False,
        )

    def build_screen_state(
        self,
        kind: str,
        *banners: UIBanner,
        disabled_actions: tuple[str, ...] = (),
    ) -> UIScreenState:
        ordered = tuple(sorted(banners, key=lambda banner: self._banner_priority(banner.code)))
        merged_disabled = set(disabled_actions)
        if any(banner.code == "key_changed" for banner in ordered):
            merged_disabled.add("send")
        return UIScreenState(
            kind=("blocked" if "send" in merged_disabled and kind != "loading" else kind),  # type: ignore[arg-type]
            banners=ordered,
            primary_banner=ordered[0] if ordered else None,
            disabled_actions=tuple(sorted(merged_disabled)),
        )

    def map_exception_to_banner(self, exc: Exception) -> UIBanner:
        if isinstance(exc, LocalStorageSecurityError):
            return LOCAL_STORAGE_UNAVAILABLE
        if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
            return NETWORK_UNAVAILABLE_BANNER
        if isinstance(exc, IntegrityError):
            return UIBanner(
                code="ciphertext_tampered",
                severity="error",
                title="Message rejected",
                message="A message failed integrity checks and was rejected.",
            )
        if isinstance(exc, SecurityError):
            if isinstance(exc, InvalidPeerBundleError):
                return PEER_BUNDLE_BLOCKED_BANNER
            return self._local_security_banner("A security policy blocked this action.")
        if isinstance(exc, IMClientError):
            return self._server_error_banner(exc.detail)
        return MESSAGE_SEND_FAILED_BANNER

    def _invalid_peer_bundle_log_key(self, *, peer_id: str, bundle: object) -> str:
        digest = hashlib.sha256()
        digest.update(peer_id.encode())
        for field_name in ("identity_pub_b64", "dh_pub_b64", "key_sig_b64"):
            value = getattr(bundle, field_name, "")
            if isinstance(value, str):
                prefix = value[:_INVALID_BUNDLE_HASH_FIELD_CHARS]
                digest.update(field_name.encode())
                digest.update(str(len(value)).encode())
                digest.update(prefix.encode())
        return digest.hexdigest()

    def _should_log_invalid_peer_bundle(self, key: str) -> bool:
        if key in self._invalid_peer_bundle_log_keys:
            self._invalid_peer_bundle_log_keys.move_to_end(key)
            return False
        self._invalid_peer_bundle_log_keys[key] = None
        if len(self._invalid_peer_bundle_log_keys) > _INVALID_BUNDLE_LOG_KEY_LIMIT:
            self._invalid_peer_bundle_log_keys.popitem(last=False)
        return True

    def _log_invalid_peer_bundle(
        self,
        *,
        event: str,
        peer_id: str,
        bundle: object,
        exc: InvalidPeerBundleError,
        conversation_id: str | None = None,
        message_id: str | None = None,
        peer_username: str | None = None,
    ) -> None:
        log_key = self._invalid_peer_bundle_log_key(peer_id=peer_id, bundle=bundle)
        if not self._should_log_invalid_peer_bundle(log_key):
            return
        log.warning(
            event,
            conversation_id=conversation_id,
            message_id=message_id,
            peer_id=peer_id,
            peer=peer_username,
            error=type(exc).__name__,
            diagnostic_key=log_key,
        )

    def trust_state_to_view_model(self, conversation_id: str) -> TrustViewModel | None:
        state = self._sessions.get(conversation_id)
        if state is None:
            return None
        trust = state.identity_key_cache.get_trust_state(state.session_key.peer_id)
        if trust is None:
            return None
        banner: UIBanner | None = None
        if trust.key_changed and trust.verified:
            banner = KEY_CHANGED_BANNER
        elif not trust.verified:
            banner = TRUST_UNVERIFIED_BANNER
        return TrustViewModel(
            fingerprint="",
            verified=trust.verified,
            key_changed=trust.key_changed,
            requires_action=(trust.key_changed or not trust.verified),
            last_verified_at=None,
            banner=banner,
        )

    def _trust_view_model_with_fingerprint(self, conversation_id: str) -> TrustViewModel | None:
        trust_view_model = self.trust_state_to_view_model(conversation_id)
        if trust_view_model is None:
            return None
        state = self._sessions.get(conversation_id)
        if state is None or self._local_keys is None:
            return trust_view_model
        peer_pub = state.identity_key_cache.get(state.session_key.peer_id)
        if peer_pub is None:
            return trust_view_model
        fingerprint = compute_fingerprint(self._local_keys.identity_kp.public_bytes(), peer_pub)
        return TrustViewModel(
            fingerprint=fingerprint,
            verified=trust_view_model.verified,
            key_changed=trust_view_model.key_changed,
            requires_action=trust_view_model.requires_action,
            last_verified_at=trust_view_model.last_verified_at,
            banner=trust_view_model.banner,
        )

    def _build_conversation_summaries(
        self,
        conversations: list[dict[str, Any]],
    ) -> list[ConversationSummaryViewModel]:
        summaries: list[ConversationSummaryViewModel] = []
        for conversation in conversations:
            trust_view_model = self.trust_state_to_view_model(conversation["id"])
            summaries.append(
                ConversationSummaryViewModel(
                    conv_id=conversation["id"],
                    peer_id=conversation["peer_id"],
                    peer_username=conversation["peer_username"],
                    unread_count=conversation.get("unread_count", 0),
                    requires_action=(
                        trust_view_model.requires_action if trust_view_model else False
                    ),
                    primary_banner=(trust_view_model.banner if trust_view_model else None),
                )
            )
        return summaries

    async def load_chat_history_state(self, conversation_id: str) -> ChatHistoryResult:
        from client.state.store import get_messages

        try:
            messages = tuple(await get_messages(conversation_id, limit=50))
            kind = "empty" if not messages else "ok"
            return ChatHistoryResult(state=self.build_screen_state(kind), messages=messages)
        except (LocalStorageSecurityError, SecurityError) as exc:
            log.warning(
                "chat_history_unavailable",
                conversation_id=conversation_id,
                error=type(exc).__name__,
            )
            return ChatHistoryResult(
                state=self.build_screen_state("degraded", LOCAL_HISTORY_UNAVAILABLE),
                messages=(),
            )

    async def _sync_chat_history_from_server(self, conversation_id: str) -> None:
        if self._client is None:
            return

        pages: list[list[MessageEnvelope]] = []
        before_id: str | None = None
        while True:
            try:
                response = await self._client.fetch_messages(conversation_id, before_id=before_id)
            except IMClientError as exc:
                log.warning(
                    "history_sync_fetch_failed",
                    conversation_id=conversation_id,
                    error=exc.detail,
                )
                return
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                log.warning(
                    "history_sync_fetch_network_error",
                    conversation_id=conversation_id,
                    error=str(exc),
                )
                return
            pages.append(list(response.messages))
            if not response.has_more or response.next_cursor is None:
                break
            before_id = response.next_cursor

        my_id = self._user_id or self._username
        for page in reversed(pages):
            for envelope in reversed(page):
                if envelope.sender_id == my_id:
                    continue
                await self._store_fetched_history_envelope(envelope)

    async def _store_fetched_history_envelope(self, envelope: MessageEnvelope) -> None:
        if self._client is None:
            return

        conv_id = envelope.conversation_id
        peer_id = envelope.sender_id
        my_id = self._user_id or self._username

        state = self._sessions.get(conv_id)
        if state is None:
            if (
                envelope.eph_pub_b64 is None
                or envelope.conv_dh_pub_b64 is None
                or self._local_keys is None
            ):
                log.warning(
                    "history_sync_session_unavailable",
                    conversation_id=conv_id,
                    message_id=envelope.id,
                )
                return

            peer_username = self._peer_usernames.get(peer_id)
            if peer_username is None:
                log.warning(
                    "history_sync_missing_peer_username",
                    conversation_id=conv_id,
                    message_id=envelope.id,
                    peer_id=peer_id,
                )
                return

            try:
                peer_bundle = await self._client.get_keys(peer_username)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                log.warning(
                    "history_sync_get_keys_network_error",
                    conversation_id=conv_id,
                    message_id=envelope.id,
                    peer=peer_username,
                    err=str(exc),
                )
                return
            except IMClientError as exc:
                log.warning(
                    "history_sync_get_keys_failed",
                    conversation_id=conv_id,
                    message_id=envelope.id,
                    peer=peer_username,
                    err=str(exc),
                )
                return

            try:
                verified_bundle = validate_and_decode_peer_bundle(peer_bundle)
                eph_pub = base64.b64decode(envelope.eph_pub_b64, validate=True)
                conv_dh_pub = base64.b64decode(envelope.conv_dh_pub_b64, validate=True)
                session_key = derive_session_key_as_responder(
                    my_identity_kp=self._local_keys.identity_kp,
                    my_dh_kp=self._local_keys.dh_kp,
                    peer_identity_pub_bytes=verified_bundle.identity_pub,
                    peer_dh_pub_bytes=verified_bundle.dh_pub,
                    eph_pub_bytes=eph_pub,
                    conv_dh_pub_bytes=conv_dh_pub,
                    my_user_id=my_id,
                    peer_user_id=peer_id,
                    conversation_id=conv_id,
                )
                send_chain, recv_chain = derive_ratchet_chains(session_key.raw, initiator=False)
            except InvalidPeerBundleError as exc:
                self._log_invalid_peer_bundle(
                    event="history_sync_peer_bundle_rejected",
                    peer_id=peer_id,
                    bundle=peer_bundle,
                    exc=exc,
                    conversation_id=conv_id,
                    message_id=envelope.id,
                    peer_username=peer_username,
                )
                return
            except (binascii.Error, ValueError) as exc:
                log.warning(
                    "history_sync_session_bootstrap_failed",
                    conversation_id=conv_id,
                    message_id=envelope.id,
                    error=type(exc).__name__,
                )
                return
            cache = IdentityKeyCache()
            check_and_update_verified_peer_bundle(cache, peer_id, verified_bundle)
            state = SessionState(
                session_key=session_key,
                send_chain=send_chain,
                recv_chain=recv_chain,
                replay_protector=ReplayProtector(),
                identity_key_cache=cache,
            )
            self._sessions[conv_id] = state
            self._persist_sessions()

        try:
            plaintext = decrypt_envelope(
                recv_chain=state.recv_chain,
                envelope=envelope,
                replay_protector=state.replay_protector,
            )
        except SecurityError as exc:
            log.warning(
                "history_sync_message_rejected",
                conversation_id=conv_id,
                message_id=envelope.id,
                error=type(exc).__name__,
            )
            return
        except ValueError as exc:
            log.warning(
                "history_sync_message_malformed",
                conversation_id=conv_id,
                message_id=envelope.id,
                err=str(exc),
            )
            return

        try:
            await save_message(
                id=envelope.id,
                conversation_id=conv_id,
                sender_id=peer_id,
                recipient_id=my_id,
                counter=envelope.counter,
                plaintext=plaintext,
                sent_at=envelope.sent_at,
                ttl_seconds=envelope.ttl_seconds,
                delivery_status="delivered",
            )
        except (LocalStorageSecurityError, SecurityError) as exc:
            log.warning(
                "history_sync_store_failed",
                conversation_id=conv_id,
                message_id=envelope.id,
                error=type(exc).__name__,
            )
            return

        self._persist_sessions()
        await upsert_conversation(
            id=conv_id,
            peer_id=peer_id,
            peer_username=self._peer_usernames.get(peer_id, peer_id),
            last_message_at=envelope.sent_at,
            unread_count=0,
        )
        await self._send_delivery_ack(envelope.id, conv_id)

    async def send_message_state(
        self,
        *,
        conversation_id: str,
        peer_id: str,
        plaintext: str,
    ) -> SendResult:
        result = await execute_send_message(
            self._send_message_context(),
            conversation_id=conversation_id,
            peer_id=peer_id,
            plaintext=plaintext,
            ensure_session_fn=lambda context, conv_id, target_peer_id: self._ensure_session(
                conv_id, target_peer_id
            ),
            build_and_encrypt_fn=build_and_encrypt,
            save_message_fn=save_message,
            upsert_conversation_fn=upsert_conversation,
        )
        if isinstance(result, SendMessageBlocked):
            log.warning(
                "send_message_blocked",
                conversation_id=conversation_id,
                peer_id=peer_id,
                error=result.reason.code,
            )
        return self._map_send_message_result(result)

    def _send_message_context(self) -> SendMessageContext:
        return SendMessageContext(
            client=self._client,
            local_keys=self._local_keys,
            username=self._username,
            user_id=self._user_id,
            sessions=self._sessions,
            peer_usernames=self._peer_usernames,
            ttl_settings=self._ttl_settings,
            counters=self._counters,
            persist_sessions=self._persist_sessions,
        )

    def _friends_context(self) -> FriendsContext:
        return FriendsContext(client=self._client)

    def _current_friends_screen(self) -> FriendsScreenActions | None:
        screen = self.screen
        if not isinstance(screen, FriendsScreenActions):
            return None
        return cast(FriendsScreenActions, screen)

    def _login_context(self) -> LoginContext:
        return LoginContext(
            server_url=self._server_url,
            verify_tls=self._verify_tls,
            ca_cert=self._ca_cert,
            pin_sha256=self._pin_sha256,
            on_ws_message=self._on_ws_message,
            client_factory=IMClient,
            keystore_exists_fn=keystore_exists,
            load_keystore_fn=load_keystore,
            derive_storage_key_fn=self._derive_and_set_storage_key,
            init_store_fn=init_store,
            sweep_expired_fn=sweep_expired,
            load_sessions_fn=load_sessions,
        )

    def _map_send_message_result(
        self,
        result: SendMessageSucceeded | SendMessageBlocked | NetworkFailure | ServerFailure,
    ) -> SendResult:
        if isinstance(result, SendMessageSucceeded):
            return SendResult(ok=True, state=self.build_screen_state("ok"), sent_at=result.sent_at)
        if isinstance(result, SendMessageBlocked):
            return SendResult(
                ok=False,
                state=self.build_screen_state(
                    "blocked",
                    self._local_security_banner(result.reason.message),
                    disabled_actions=("send",) if result.disable_send else (),
                ),
                sent_at=result.sent_at,
            )
        if isinstance(result, NetworkFailure):
            return SendResult(
                ok=False,
                state=self.build_screen_state("error", NETWORK_UNAVAILABLE_BANNER),
            )
        return SendResult(
            ok=False,
            state=self.build_screen_state(
                "error",
                self._server_error_banner(result.message),
            ),
        )

    def _apply_login_result(
        self,
        *,
        result: LoginSucceeded | LoginFailed,
        login_screen: Any,
    ) -> bool:
        if isinstance(result, LoginFailed):
            login_screen.query_one("#error", Static).update(result.message)
            self._client = None
            return False

        self._client = cast(IMClient, result.client)
        self._password = result.password
        self._username = result.username
        self._local_keys = result.local_keys
        self._user_id = result.user_id
        self._sessions = result.sessions
        self._counters = self._rehydrate_outbound_counters(result.sessions)
        return True

    @staticmethod
    def _apply_pending_requests_result(
        *,
        friends: Any,
        result: PendingRequestsLoaded | NetworkFailure | ServerFailure,
    ) -> None:
        if isinstance(result, PendingRequestsLoaded):
            friends.populate_pending(result.requests)
            return
        friends.show_error(result.message)

    @staticmethod
    def _apply_friend_request_result(
        *,
        friends: Any,
        result: FriendRequestHandled | NetworkFailure | ServerFailure,
    ) -> None:
        if isinstance(result, FriendRequestHandled):
            friends.populate_pending(result.requests)
            friends.show_status(result.status_message)
            return
        friends.show_error(result.message)

    @staticmethod
    def _apply_send_friend_request_result(
        *,
        friends: Any,
        result: SendFriendRequestResult,
    ) -> None:
        if isinstance(result, FriendRequestSent):
            friends.show_status(result.status_message)
            return
        friends.show_error(result.message)

    async def _refresh_open_conversation_lists(self) -> None:
        await self._sync_conversations_from_server()
        conversations = await get_conversations()
        self._rehydrate_peer_usernames(conversations)

        for screen in self.screen_stack:
            if isinstance(screen, ConversationListScreen):
                screen.populate(self._build_conversation_summaries(conversations))

    def compose(self) -> ComposeResult:
        yield from []  # app has no persistent widgets; screens handle layout

    def on_mount(self) -> None:
        self.push_screen(LoginScreen(prefill_username=self._username))

    async def on_login_screen_exit(self, _: LoginScreen.Exit) -> None:
        self.exit()

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    async def on_login_screen_login_success(self, msg: LoginScreen.LoginSuccess) -> None:
        """User submitted login form — attempt login."""
        username = msg.username
        password = msg.password
        totp_code = msg.totp_code

        # Capture screen reference NOW before any await displaces it
        login_screen = self.screen  # LoginScreen is current screen at this point

        result = await execute_login_handshake(
            self._login_context(),
            username=username,
            password=password,
            totp_code=totp_code,
        )
        if not self._apply_login_result(result=result, login_screen=login_screen):
            return

        await self._show_conversations()

    async def _show_conversations(self) -> None:
        await self._sync_conversations_from_server()
        convs = await get_conversations()
        self._rehydrate_peer_usernames(convs)
        screen = ConversationListScreen(my_username=self._username)
        await self.push_screen(screen)
        screen.populate(self._build_conversation_summaries(convs))

    async def _sync_conversations_from_server(self) -> None:
        if self._client is None:
            return
        try:
            response = await self._client.list_conversations()
        except IMClientError as exc:
            log.warning("list_conversations_failed", err=exc.detail)
            return
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning("list_conversations_network_error", err=str(exc))
            return

        for conversation in response.conversations:
            await upsert_conversation(
                id=conversation.id,
                peer_id=conversation.peer_id,
                peer_username=conversation.peer_username,
                last_message_at=conversation.last_message_at,
                unread_count=conversation.unread_count,
            )

    def _rehydrate_peer_usernames(self, conversations: list[dict[str, Any]]) -> None:
        self._peer_usernames = {
            conversation["peer_id"]: conversation["peer_username"] for conversation in conversations
        }

    # ------------------------------------------------------------------
    # Registration flow
    # ------------------------------------------------------------------

    async def on_register_screen_register_request(self, msg: Any) -> None:
        username = msg.username
        password = msg.password
        register_screen = self.screen

        identity_kp = IdentityKeypair.generate()
        dh_kp = DHKeypair.generate()
        key_sig = make_key_signature(identity_kp, dh_kp)
        local_keys = LocalKeys(identity_kp=identity_kp, dh_kp=dh_kp, key_sig=key_sig)

        client = IMClient(
            self._server_url,
            verify_tls=self._verify_tls,
            ca_cert=self._ca_cert,
            pin_sha256=self._pin_sha256,
        )
        async with client:
            try:
                resp = await client.register(
                    RegisterRequest(
                        username=username,
                        password=password,
                        identity_pub_b64=identity_kp.public_b64(),
                        dh_pub_b64=dh_kp.public_b64(),
                        key_sig_b64=base64.b64encode(key_sig).decode(),
                    )
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                self._render_register_error(
                    register_screen,
                    f"Cannot reach server — is it running? ({type(e).__name__})",
                )
                return
            except IMClientError as e:
                self._render_register_error(register_screen, f"Registration failed: {e.detail}")
                return
            except ValidationError as e:
                self._render_register_error(
                    register_screen,
                    f"Registration failed: {_clean_validation_error_message(e.errors()[0]['msg'])}",
                )
                return

        save_keystore(username, password, local_keys)

        # Show QR + OTP verify step on the same screen instead of a fleeting toast
        from client.ui.screens.register import RegisterScreen

        if isinstance(self.screen, RegisterScreen):
            self.screen.show_totp_setup(resp.totp_provisioning_uri)

    async def on_register_screen_totp_verify(self, msg: Any) -> None:
        """User confirmed their TOTP code — pop back to the login screen."""
        self.pop_screen()

    # ------------------------------------------------------------------
    # Conversation list events
    # ------------------------------------------------------------------

    async def on_conversation_list_screen_conversation_selected(
        self, msg: ConversationListScreen.ConversationSelected
    ) -> None:
        self._peer_usernames[msg.peer_id] = msg.peer_username
        result = await execute_open_conversation(
            OpenConversationContext(
                client=self._client,
                reset_unread_fn=reset_unread,
            ),
            conversation_id=msg.conv_id,
        )
        if not isinstance(result, Success):
            log.warning(
                "open_conversation_mark_read_failed",
                conversation_id=msg.conv_id,
                error=result.message,
            )
        from client.ui.screens.chat import ChatScreen

        screen = ChatScreen(
            conversation_id=msg.conv_id,
            peer_id=msg.peer_id,
            peer_username=msg.peer_username,
            my_user_id=self._user_id or self._username,
        )
        await self.push_screen(screen)

    async def on_conversation_list_screen_open_friends(
        self, msg: ConversationListScreen.OpenFriends
    ) -> None:
        from client.ui.screens.friends import FriendsScreen

        await self.push_screen(FriendsScreen())

    async def on_conversation_list_screen_logout(self, msg: ConversationListScreen.Logout) -> None:
        if self._client:
            unexpected_logout_error: Exception | None = None
            try:
                await self._client.logout()
            except (IMClientError, httpx.HTTPError) as exc:
                log.warning("logout_failed", err=str(exc))
            except Exception as exc:
                unexpected_logout_error = exc
            finally:
                await self._client.__aexit__(None, None, None)
                self._client = None
            if unexpected_logout_error is not None:
                raise unexpected_logout_error
        self._persist_sessions()
        self.pop_screen()

    # ------------------------------------------------------------------
    # Chat screen events
    # ------------------------------------------------------------------

    async def on_chat_screen_send_message(self, msg: Any) -> None:
        from client.ui.screens.chat import ChatScreen

        chat = self.screen
        if not isinstance(chat, ChatScreen):
            return

        conv_id = chat.conversation_id
        peer_id = chat.peer_id

        try:
            result = await self.send_message_state(
                conversation_id=conv_id,
                peer_id=peer_id,
                plaintext=msg.text,
            )
        except IMClientError as e:
            chat.apply_send_result(
                SendResult(
                    ok=False,
                    state=self.build_screen_state(
                        "error",
                        self.map_exception_to_banner(e),
                    ),
                )
            )
            return
        chat.apply_send_result(result)
        if not result.ok:
            return
        if result.sent_at is None:
            return
        ttl = self._ttl_settings.get(conv_id)
        chat.add_message(
            self._user_id or self._username, msg.text, result.sent_at, "sent", ttl, is_mine=True
        )

    # ------------------------------------------------------------------
    # Friends screen events
    # ------------------------------------------------------------------

    async def on_friends_screen__load_pending(self, msg: Any) -> None:
        """Handler for FriendsScreen._LoadPending (internal load trigger)."""
        if self._client is None:
            return
        friends = self._current_friends_screen()
        if friends is None:
            return
        result = await execute_load_pending_requests(self._friends_context())
        friends = self._current_friends_screen()
        if friends is None:
            return
        self._apply_pending_requests_result(friends=friends, result=result)
        if not isinstance(result, PendingRequestsLoaded):
            log.warning("friends_pending_load_failed", err=result.message)

    async def on_friends_screen_send_request(self, msg: Any) -> None:
        if self._client is None:
            return
        friends = self._current_friends_screen()
        if friends is None:
            log.warning("friends_screen_lookup_failed", err="FriendsScreen not active")
            return
        result = await execute_send_friend_request(
            self._friends_context(),
            username=msg.username,
        )
        self._apply_send_friend_request_result(friends=friends, result=result)
        if isinstance(result, FriendRequestSent):
            return
        log.warning("friend_request_send_failed", username=msg.username, err=result.message)

    async def on_friends_screen_accept_request(self, msg: Any) -> None:
        if self._client is None:
            return
        friends = self._current_friends_screen()
        if friends is None:
            log.warning("friends_screen_lookup_failed", err="FriendsScreen not active")
            return
        result = await execute_handle_friend_request(
            self._friends_context(),
            request_id=msg.request_id,
            action="accept",
        )
        self._apply_friend_request_result(friends=friends, result=result)
        if isinstance(result, FriendRequestHandled):
            await self._refresh_open_conversation_lists()
            return
        if not isinstance(result, FriendRequestHandled):
            log.warning(
                "friend_request_accept_failed",
                request_id=msg.request_id,
                err=result.message,
            )

    async def on_friends_screen_decline_request(self, msg: Any) -> None:
        if self._client is None:
            return
        friends = self._current_friends_screen()
        if friends is None:
            log.warning("friends_screen_lookup_failed", err="FriendsScreen not active")
            return
        result = await execute_handle_friend_request(
            self._friends_context(),
            request_id=msg.request_id,
            action="decline",
        )
        self._apply_friend_request_result(friends=friends, result=result)
        if not isinstance(result, FriendRequestHandled):
            log.warning(
                "friend_request_decline_failed",
                request_id=msg.request_id,
                err=result.message,
            )

    async def on_chat_screen_request_history(self, msg: Any) -> None:
        from client.ui.screens.chat import ChatScreen

        chat = self.screen
        if not isinstance(chat, ChatScreen):
            return
        await self._sync_chat_history_from_server(msg.conversation_id)
        result = await self.load_chat_history_state(msg.conversation_id)
        trust_state = self.trust_state_to_view_model(msg.conversation_id)
        if trust_state and trust_state.banner is not None:
            result = ChatHistoryResult(
                state=self.build_screen_state(
                    result.state.kind, *(result.state.banners + (trust_state.banner,))
                ),
                messages=result.messages,
            )
        chat.render_history_result(result)

    # ------------------------------------------------------------------
    # Settings screen events
    # ------------------------------------------------------------------

    async def on_settings_screen_set_ttl(self, msg: Any) -> None:
        from client.ui.screens.chat import ChatScreen

        # Settings is pushed on top of chat — chat is second from top
        stack = self.screen_stack
        chat = stack[-2] if len(stack) >= 2 else None
        if isinstance(chat, ChatScreen):
            self._ttl_settings[chat.conversation_id] = msg.ttl_seconds

    async def on_settings_screen__request_fingerprint(self, msg: Any) -> None:
        """Handler for SettingsScreen._RequestFingerprint."""
        from client.ui.screens.settings import SettingsScreen

        settings_screen = self.screen
        if not isinstance(settings_screen, SettingsScreen):
            return
        trust_view_model = self._trust_view_model_with_fingerprint(msg.conversation_id)
        if trust_view_model is not None:
            settings_screen.set_trust_view_model(trust_view_model)

    async def on_settings_screen_mark_verified(self, msg: Any) -> None:
        from client.ui.screens.chat import ChatScreen
        from client.ui.screens.settings import SettingsScreen

        settings_screen = self.screen
        stack = self.screen_stack
        chat = stack[-2] if len(stack) >= 2 else None
        if isinstance(chat, ChatScreen):
            state = self._sessions.get(chat.conversation_id)
            if state:
                peer_pub = state.identity_key_cache.get(chat.peer_id)
                if peer_pub:
                    state.identity_key_cache.mark_verified(chat.peer_id, peer_pub)
                    self._persist_sessions()
                    chat.clear_key_warning()
                    if isinstance(settings_screen, SettingsScreen):
                        trust_view_model = self._trust_view_model_with_fingerprint(
                            chat.conversation_id
                        )
                        if trust_view_model is not None:
                            settings_screen.set_trust_view_model(trust_view_model)

    # ------------------------------------------------------------------
    # WebSocket message handler
    # ------------------------------------------------------------------

    async def _on_ws_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "message":
            await self._handle_incoming_message(msg.get("payload", {}))
        elif msg_type == "ack":
            await self._handle_ack(msg.get("payload", {}))
        elif msg_type == "friend_request":
            await self._handle_friend_request_push(msg.get("payload", {}))

    async def _handle_friend_request_push(self, payload: dict[str, Any]) -> None:
        try:
            push = FriendRequestPushPayload.model_validate(payload)
        except ValidationError as exc:
            log.warning("friend_request_push_invalid", err=str(exc))
            return

        if push.event != FriendRequestStatus.ACCEPTED:
            return

        await self._refresh_open_conversation_lists()

    async def _handle_incoming_message(self, payload: dict) -> None:
        try:
            envelope = MessageEnvelope.model_validate(payload)
        except ValidationError as exc:
            log.warning("incoming_message_invalid", err=str(exc))
            return

        # if we haven't established a network client nothing can proceed
        if self._client is None:
            return

        conv_id = envelope.conversation_id
        peer_id = envelope.sender_id
        my_id = self._user_id or self._username

        # Derive session if we don't have one yet (first message from this peer)
        state = self._sessions.get(conv_id)
        if state is None:
            if (
                envelope.eph_pub_b64 is None
                or envelope.conv_dh_pub_b64 is None
                or self._local_keys is None
            ):
                return

            # Look up peer keys by username if we have it, else by user_id
            peer_username = self._peer_usernames.get(peer_id)
            if peer_username is None:
                return  # can't look up keys without username; peer must be in contacts

            try:
                peer_bundle = await self._client.get_keys(peer_username)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                log.warning("get_keys network error", peer=peer_username, err=str(e))
                return
            except IMClientError as e:
                log.warning("get_keys failed", peer=peer_username, err=str(e))
                return

            try:
                verified_bundle = validate_and_decode_peer_bundle(peer_bundle)
                eph_pub = base64.b64decode(envelope.eph_pub_b64, validate=True)
                conv_dh_pub = base64.b64decode(envelope.conv_dh_pub_b64, validate=True)
                session_key = derive_session_key_as_responder(
                    my_identity_kp=self._local_keys.identity_kp,
                    my_dh_kp=self._local_keys.dh_kp,
                    peer_identity_pub_bytes=verified_bundle.identity_pub,
                    peer_dh_pub_bytes=verified_bundle.dh_pub,
                    eph_pub_bytes=eph_pub,
                    conv_dh_pub_bytes=conv_dh_pub,
                    my_user_id=my_id,
                    peer_user_id=peer_id,
                    conversation_id=conv_id,
                )
                send_chain, recv_chain = derive_ratchet_chains(session_key.raw, initiator=False)
            except InvalidPeerBundleError as exc:
                self._log_invalid_peer_bundle(
                    event="incoming_peer_bundle_rejected",
                    peer_id=peer_id,
                    bundle=peer_bundle,
                    exc=exc,
                    conversation_id=conv_id,
                    message_id=envelope.id,
                    peer_username=peer_username,
                )
                return
            except (binascii.Error, ValueError) as exc:
                log.warning(
                    "incoming_session_bootstrap_failed",
                    conversation_id=conv_id,
                    message_id=envelope.id,
                    error=type(exc).__name__,
                )
                return

            cache = IdentityKeyCache()
            check_and_update_verified_peer_bundle(cache, peer_id, verified_bundle)
            state = SessionState(
                session_key=session_key,
                send_chain=send_chain,
                recv_chain=recv_chain,
                replay_protector=ReplayProtector(),
                identity_key_cache=cache,
                next_outbound_counter=0,
            )
            self._sessions[conv_id] = state
            self._persist_sessions()

        # Key change check on re-keying messages
        key_changed = False
        if envelope.eph_pub_b64:
            peer_username = self._peer_usernames.get(peer_id)
            client = self._client
            if peer_username and client:
                try:
                    peer_bundle = await client.get_keys(peer_username)
                    verified_bundle = validate_and_decode_peer_bundle(peer_bundle)
                    key_changed = check_and_update_verified_peer_bundle(
                        state.identity_key_cache, peer_id, verified_bundle
                    )
                    self._persist_sessions()
                except KeyChangeWarning:
                    key_changed = True
                    self._persist_sessions()
                except InvalidPeerBundleError as exc:
                    self._log_invalid_peer_bundle(
                        event="key_bundle_refresh_rejected",
                        peer_id=peer_id,
                        bundle=peer_bundle,
                        exc=exc,
                        conversation_id=conv_id,
                        message_id=envelope.id,
                        peer_username=peer_username,
                    )
                except IMClientError as exc:
                    log.warning("key_bundle_refresh_failed", peer=peer_username, err=str(exc))
                except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                    log.warning(
                        "key_bundle_refresh_network_error", peer=peer_username, err=str(exc)
                    )

        try:
            plaintext = decrypt_envelope(
                recv_chain=state.recv_chain,
                envelope=envelope,
                replay_protector=state.replay_protector,
            )
        except SecurityError as exc:
            log.warning(
                "incoming_message_rejected",
                conversation_id=conv_id,
                message_id=envelope.id,
                error=type(exc).__name__,
            )
            return
        except ValueError as exc:
            log.warning(
                "incoming_message_malformed",
                conversation_id=conv_id,
                message_id=envelope.id,
                err=str(exc),
            )
            return

        from client.ui.screens.chat import ChatScreen

        active_chat = self.screen if isinstance(self.screen, ChatScreen) else None

        # Persist locally
        try:
            await save_message(
                id=envelope.id,
                conversation_id=conv_id,
                sender_id=peer_id,
                recipient_id=my_id,
                counter=envelope.counter,
                plaintext=plaintext,
                sent_at=envelope.sent_at,
                ttl_seconds=envelope.ttl_seconds,
                delivery_status="delivered",
            )
        except (LocalStorageSecurityError, SecurityError) as exc:
            log.warning(
                "incoming_message_store_failed",
                conversation_id=conv_id,
                message_id=envelope.id,
                error=type(exc).__name__,
            )
            if active_chat and active_chat.conversation_id == conv_id:
                active_chat.set_banner(LOCAL_HISTORY_UNAVAILABLE)
            return
        self._persist_sessions()

        peer_username = self._peer_usernames.get(peer_id, peer_id)
        await upsert_conversation(
            id=conv_id,
            peer_id=peer_id,
            peer_username=peer_username,
            last_message_at=envelope.sent_at,
            unread_count=0,  # will be incremented below if not active
        )

        # Route to active chat or increment unread
        if active_chat and active_chat.conversation_id == conv_id:
            active_chat.add_message(
                peer_id,
                plaintext,
                envelope.sent_at,
                "delivered",
                envelope.ttl_seconds,
                is_mine=False,
            )
            if key_changed:
                active_chat.apply_send_result(
                    SendResult(
                        ok=False,
                        state=self.build_screen_state("blocked", KEY_CHANGED_BANNER),
                    )
                )
        else:
            await increment_unread(conv_id)
            for screen in self.screen_stack:
                if isinstance(screen, ConversationListScreen):
                    convs = await get_conversations()
                    conv = next((c for c in convs if c["id"] == conv_id), None)
                    if conv:
                        trust_view_model = self.trust_state_to_view_model(conv_id)
                        screen.refresh_conversation(
                            conv_id,
                            conv["unread_count"],
                            requires_action=(
                                trust_view_model.requires_action if trust_view_model else False
                            ),
                            primary_banner=(trust_view_model.banner if trust_view_model else None),
                        )

        # Send delivery ACK
        await self._send_delivery_ack(envelope.id, conv_id)

    async def _handle_ack(self, payload: dict) -> None:
        message_id = payload.get("message_id")
        if not message_id:
            return
        await update_delivery_status(message_id, "delivered")

    async def _send_delivery_ack(self, message_id: str, conversation_id: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.send_ack(
                DeliveryAck(
                    message_id=message_id,
                    conversation_id=conversation_id,
                )
            )
        except IMClientError as exc:
            log.warning("delivery_ack_failed", message_id=message_id, err=str(exc))
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning(
                "delivery_ack_network_error",
                message_id=message_id,
                err=str(exc),
            )

    # ------------------------------------------------------------------
    # Session establishment helper
    # ------------------------------------------------------------------

    async def _ensure_session(self, conv_id: str, peer_id: str) -> SessionState | None:
        return await ensure_send_session(self._send_message_context(), conv_id, peer_id)

    # ------------------------------------------------------------------
    # Cleanup on exit
    # ------------------------------------------------------------------

    async def on_unmount(self) -> None:
        self._persist_sessions()
        if self._client:
            await self._client.__aexit__(None, None, None)
