"""
client/ui/app.py — Main Textual TUI application.

Wires together all screens, IMClient, crypto, and local storage.
Handles all message events from screens and dispatches WebSocket pushes.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import ValidationError
from textual.app import App, ComposeResult
from textual.widgets import Static

from client.api.client import IMClient, IMClientError
from client.crypto.session import (
    DHKeypair,
    IdentityKeyCache,
    IdentityKeypair,
    IntegrityError,
    KeyChangeWarning,
    LocalStorageSecurityError,
    ReplayProtector,
    SecurityError,
    build_and_encrypt,
    compute_fingerprint,
    decrypt_envelope,
    derive_ratchet_chains,
    derive_session_key_as_initiator,
    derive_session_key_as_responder,
    make_key_signature,
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
from client.ui.screens.conversations import ConversationListScreen
from client.ui.screens.login import LoginScreen
from client.ui.contracts import ChatHistoryResult, SendResult, TrustDisplayState, UIErrorState
from shared.protocol import (
    DeliveryAck,
    LoginRequest,
    MessageEnvelope,
    RegisterRequest,
)

log = structlog.get_logger()

LOCAL_HISTORY_UNAVAILABLE = UIErrorState(
    code="local_history_unavailable",
    message="Local chat history is unavailable on this device.",
    severity="warning",
)
LOCAL_KEY_UNAVAILABLE = UIErrorState(
    code="local_key_unavailable",
    message="Local secure storage is unavailable. Message was not sent.",
    severity="error",
)


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
        self._username   = username
        self._verify_tls = verify_tls
        self._ca_cert    = ca_cert
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

    def _persist_sessions(self) -> None:
        if self._local_keys and self._password and self._username:
            save_sessions(self._username, self._password, self._sessions)

    def _derive_and_set_storage_key(self, username: str, password: str) -> None:
        keystore_path = Path.home() / ".comp3334im" / username / "keystore.json"
        import json

        keystore_data = json.loads(keystore_path.read_text())
        salt = base64.b64decode(keystore_data["argon2_salt_b64"])
        storage_key = _derive_storage_key(password, salt)
        set_storage_key(storage_key)

    def _trust_state_for(self, conversation_id: str) -> TrustDisplayState | None:
        state = self._sessions.get(conversation_id)
        if state is None:
            return None
        trust = state.identity_key_cache.get_trust_state(state.session_key.peer_id)
        if trust is None:
            return None
        return TrustDisplayState(
            verified=trust.verified,
            key_changed=trust.key_changed,
        )

    async def load_chat_history(self, conversation_id: str) -> ChatHistoryResult:
        from client.state.store import get_messages

        try:
            return ChatHistoryResult(messages=await get_messages(conversation_id, limit=50))
        except (LocalStorageSecurityError, SecurityError) as exc:
            log.warning(
                "chat_history_unavailable",
                conversation_id=conversation_id,
                error=type(exc).__name__,
            )
            return ChatHistoryResult(messages=[], error=LOCAL_HISTORY_UNAVAILABLE)

    async def send_message_action(
        self,
        *,
        conversation_id: str,
        peer_id: str,
        plaintext: str,
    ) -> SendResult:
        if self._client is None:
            return SendResult(status="failed")

        ttl = self._ttl_settings.get(conversation_id)
        counter = self._counters.get(conversation_id, 0)
        my_id = self._user_id or self._username
        session_state = await self._ensure_session(conversation_id, peer_id)
        if session_state is None:
            return SendResult(status="failed")

        sent_at = int(time.time())
        envelope = build_and_encrypt(
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
            await self._client.send_message(envelope)
            await save_message(
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
        except (LocalStorageSecurityError, SecurityError) as exc:
            log.warning(
                "send_message_blocked",
                conversation_id=conversation_id,
                peer_id=peer_id,
                error=type(exc).__name__,
            )
            return SendResult(status="blocked", sent_at=sent_at, error=LOCAL_KEY_UNAVAILABLE)
        except IMClientError:
            raise

        self._counters[conversation_id] = counter + 1
        await upsert_conversation(
            id=conversation_id,
            peer_id=peer_id,
            peer_username=self._peer_usernames.get(peer_id, peer_id),
            last_message_at=sent_at,
            unread_count=0,
        )
        return SendResult(status="sent", sent_at=sent_at)

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
        username  = msg.username
        password  = msg.password
        totp_code = msg.totp_code

        # Capture screen reference NOW before any await displaces it
        login_screen = self.screen  # LoginScreen is current screen at this point

        self._client = IMClient(self._server_url, verify_tls=self._verify_tls, ca_cert=self._ca_cert, pin_sha256=self._pin_sha256)
        await self._client.__aenter__()

        try:
            await self._client.login(
                LoginRequest(username=username, password=password, totp_code=totp_code)
            )
        except IMClientError as e:
            login_screen.query_one("#error", Static).update(f"Login failed: {e.detail}")
            await self._client.__aexit__(None, None, None)
            self._client = None
            return
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("login_network_error", username=username, err=str(e))
            login_screen.query_one("#error", Static).update(f"Cannot reach server — is it running? ({type(e).__name__})")
            await self._client.__aexit__(None, None, None)
            self._client = None
            return
        except Exception as e:
            log.warning("login_unexpected_error", username=username, err=str(e))
            login_screen.query_one("#error", Static).update(f"Unexpected error: {e}")
            await self._client.__aexit__(None, None, None)
            self._client = None
            return

        self._password = password
        self._username = username

        # Load local keys
        if not keystore_exists(username):
            login_screen.query_one("#error", Static).update(
                "No local keys found. Register first."
            )
            return

        try:
            self._local_keys = load_keystore(username, password)
            self._derive_and_set_storage_key(username, password)
        except ValueError:
            login_screen.query_one("#error", Static).update("Wrong password or corrupted keystore.")
            return

        # Init local store and sweep expired messages (R11)
        await init_store(username)
        await sweep_expired()
        self._sessions = load_sessions(username, password)

        # Fetch own user_id
        try:
            bundle = await self._client.get_keys(username)
            self._user_id = bundle.user_id
        except (IMClientError, httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            self._user_id = username

        # Connect WebSocket
        try:
            await self._client.connect_ws(self._on_ws_message)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, Exception) as e:
            log.warning(
                "websocket_connect_failed",
                username=username,
                error=type(e).__name__,
                err=str(e),
            )
            login_screen.query_one("#error", Static).update(
                f"WebSocket connection failed: {type(e).__name__} — is server running?"
            )
            await self._client.__aexit__(None, None, None)
            self._client = None
            return

        await self._show_conversations()

    async def _show_conversations(self) -> None:
        convs = await get_conversations()
        screen = ConversationListScreen(my_username=self._username)
        await self.push_screen(screen)
        screen.populate(convs)

    # ------------------------------------------------------------------
    # Registration flow
    # ------------------------------------------------------------------

    async def on_register_screen_register_request(self, msg: Any) -> None:
        username = msg.username
        password = msg.password

        identity_kp = IdentityKeypair.generate()
        dh_kp       = DHKeypair.generate()
        key_sig     = make_key_signature(identity_kp, dh_kp)
        local_keys  = LocalKeys(identity_kp=identity_kp, dh_kp=dh_kp, key_sig=key_sig)

        client = IMClient(self._server_url, verify_tls=self._verify_tls, ca_cert=self._ca_cert, pin_sha256=self._pin_sha256)
        async with client:
            try:
                resp = await client.register(RegisterRequest(
                    username=username,
                    password=password,
                    identity_pub_b64=identity_kp.public_b64(),
                    dh_pub_b64=dh_kp.public_b64(),
                    key_sig_b64=base64.b64encode(key_sig).decode(),
                ))
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                try:
                    self.screen.query_one("#error", Static).update(
                        f"Cannot reach server — is it running? ({type(e).__name__})"
                    )
                except Exception as exc:  # allow-silent-except
                    log.warning("register_error_render_failed", err=str(exc))
                return
            except IMClientError as e:
                try:
                    self.screen.query_one("#error", Static).update(
                        f"Registration failed: {e.detail}"
                    )
                except Exception as exc:  # allow-silent-except
                    log.warning("register_error_render_failed", err=str(exc))
                return
            except Exception as e:
                try:
                    self.screen.query_one("#error", Static).update(
                        f"Unexpected error: {e}"
                    )
                except Exception as exc:  # allow-silent-except
                    log.warning("register_error_render_failed", err=str(exc))
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
        await reset_unread(msg.conv_id)
        if self._client:
            await self._client.mark_read(msg.conv_id)
        self._peer_usernames[msg.peer_id] = msg.peer_username
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

    async def on_conversation_list_screen_logout(
        self, msg: ConversationListScreen.Logout
    ) -> None:
        if self._client:
            try:
                await self._client.logout()
            except Exception as exc:  # allow-silent-except
                log.warning("logout_failed", err=str(exc))
            await self._client.__aexit__(None, None, None)
            self._client = None
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
            result = await self.send_message_action(
                conversation_id=conv_id,
                peer_id=peer_id,
                plaintext=msg.text,
            )
        except IMClientError as e:
            chat.add_message("system", f"Send failed: {e.detail}", int(time.time()), "sent", None, False)
            return
        chat.apply_send_result(result)
        if result.status == "failed":
            chat.add_message("system", "Could not establish session.", int(time.time()), "sent", None, False)
            return
        if result.status == "blocked":
            return
        if result.sent_at is None:
            return
        ttl = self._ttl_settings.get(conv_id)
        chat.add_message(self._user_id or self._username, msg.text, result.sent_at, "sent", ttl, is_mine=True)

    # ------------------------------------------------------------------
    # Friends screen events
    # ------------------------------------------------------------------

    async def on_friends_screen__load_pending(self, msg: Any) -> None:
        """Handler for FriendsScreen._LoadPending (internal load trigger)."""
        from client.ui.screens.friends import FriendsScreen
        if self._client is None:
            return
        try:
            requests = await self._client.list_pending_requests()
            try:
                friends = self.screen
                friends.populate_pending([r.model_dump() for r in requests])
            except Exception as exc:  # allow-silent-except
                log.warning("friends_screen_update_failed", err=str(exc))
        except (IMClientError, httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning("friends_pending_load_failed", err=str(exc))

    async def on_friends_screen_send_request(self, msg: Any) -> None:
        if self._client is None:
            return
        try:
            friends = self.screen
        except Exception as exc:  # allow-silent-except
            log.warning("friends_screen_lookup_failed", err=str(exc))
            return
        try:
            await self._client.send_friend_request(msg.username)
            friends.show_status(f"Request sent to {msg.username}.")
        except IMClientError as e:
            friends.show_error(f"Failed: {e.detail}")

    async def on_friends_screen_accept_request(self, msg: Any) -> None:
        from client.ui.screens.friends import FriendsScreen
        if self._client is None:
            return
        try:
            friends = self.screen
            if friends is None:
                return
            await self._client.handle_friend_request(msg.request_id, "accept")
            requests = await self._client.list_pending_requests()
            friends.populate_pending([r.model_dump() for r in requests])
        except (IMClientError, httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning("friend_request_accept_failed", request_id=msg.request_id, err=str(exc))

    async def on_friends_screen_decline_request(self, msg: Any) -> None:
        from client.ui.screens.friends import FriendsScreen
        if self._client is None:
            return
        try:
            friends = self.screen
            if friends is None:
                return
            await self._client.handle_friend_request(msg.request_id, "decline")
            requests = await self._client.list_pending_requests()
            friends.populate_pending([r.model_dump() for r in requests])
        except (IMClientError, httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning("friend_request_decline_failed", request_id=msg.request_id, err=str(exc))

    async def on_chat_screen_request_history(self, msg: Any) -> None:
        from client.ui.screens.chat import ChatScreen

        chat = self.screen
        if not isinstance(chat, ChatScreen):
            return
        result = await self.load_chat_history(msg.conversation_id)
        chat.render_history_result(result)
        trust_state = self._trust_state_for(msg.conversation_id)
        if trust_state and trust_state.key_changed:
            chat.show_key_warning(chat.peer_username)

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
        state = self._sessions.get(msg.conversation_id)
        if state and self._local_keys:
            peer_pub = state.identity_key_cache.get(state.session_key.peer_id)
            if peer_pub:
                fp = compute_fingerprint(self._local_keys.identity_kp.public_bytes(), peer_pub)
                settings_screen.set_fingerprint(fp)
            trust_state = self._trust_state_for(msg.conversation_id)
            if trust_state is not None:
                settings_screen.set_trust_state(trust_state)

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
                        trust_state = self._trust_state_for(chat.conversation_id)
                        if trust_state is not None:
                            settings_screen.set_trust_state(trust_state)

    # ------------------------------------------------------------------
    # WebSocket message handler
    # ------------------------------------------------------------------

    async def _on_ws_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "message":
            await self._handle_incoming_message(msg.get("payload", {}))
        elif msg_type == "ack":
            await self._handle_ack(msg.get("payload", {}))

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
        my_id   = self._user_id or self._username

        # Derive session if we don't have one yet (first message from this peer)
        state = self._sessions.get(conv_id)
        if state is None:
            if envelope.eph_pub_b64 is None or envelope.conv_dh_pub_b64 is None or self._local_keys is None:
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

            peer_identity_pub = base64.b64decode(peer_bundle.identity_pub_b64)
            peer_dh_pub       = base64.b64decode(peer_bundle.dh_pub_b64)
            eph_pub           = base64.b64decode(envelope.eph_pub_b64)
            conv_dh_pub       = base64.b64decode(envelope.conv_dh_pub_b64)

            session_key = derive_session_key_as_responder(
                my_identity_kp=self._local_keys.identity_kp,
                my_dh_kp=self._local_keys.dh_kp,
                peer_identity_pub_bytes=peer_identity_pub,
                peer_dh_pub_bytes=peer_dh_pub,
                eph_pub_bytes=eph_pub,
                conv_dh_pub_bytes=conv_dh_pub,
                my_user_id=my_id,
                peer_user_id=peer_id,
                conversation_id=conv_id,
            )
            send_chain, recv_chain = derive_ratchet_chains(session_key.raw, initiator=False)
            cache = IdentityKeyCache()
            cache.check_and_update(peer_id, peer_identity_pub)
            state = SessionState(
                session_key=session_key,
                send_chain=send_chain,
                recv_chain=recv_chain,
                replay_protector=ReplayProtector(),
                identity_key_cache=cache,
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
                    peer_identity_pub = base64.b64decode(peer_bundle.identity_pub_b64)
                    key_changed = state.identity_key_cache.check_and_update(
                        peer_id, peer_identity_pub
                    )
                    self._persist_sessions()
                except KeyChangeWarning:
                    key_changed = True
                    self._persist_sessions()
                except IMClientError as exc:
                    log.warning("key_bundle_refresh_failed", peer=peer_username, err=str(exc))
                except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                    log.warning("key_bundle_refresh_network_error", peer=peer_username, err=str(exc))

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
                active_chat.set_ui_error(LOCAL_HISTORY_UNAVAILABLE)
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
                peer_id, plaintext, envelope.sent_at, "delivered",
                envelope.ttl_seconds, is_mine=False,
            )
            if key_changed:
                active_chat.show_key_warning(peer_username)
        else:
            await increment_unread(conv_id)
            for screen in self.screen_stack:
                if isinstance(screen, ConversationListScreen):
                    convs = await get_conversations()
                    conv = next((c for c in convs if c["id"] == conv_id), None)
                    if conv:
                        screen.refresh_conversation(conv_id, conv["unread_count"])

        # Send delivery ACK
        if self._client:
            try:
                await self._client.send_ack(
                    DeliveryAck(
                        message_id=envelope.id,
                        conversation_id=conv_id,
                    )
                )
            except IMClientError as exc:
                log.warning("delivery_ack_failed", message_id=envelope.id, err=str(exc))
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                log.warning(
                    "delivery_ack_network_error",
                    message_id=envelope.id,
                    err=str(exc),
                )

    async def _handle_ack(self, payload: dict) -> None:
        message_id = payload.get("message_id")
        if not message_id:
            return
        await update_delivery_status(message_id, "delivered")

    # ------------------------------------------------------------------
    # Session establishment helper
    # ------------------------------------------------------------------

    async def _ensure_session(self, conv_id: str, peer_id: str) -> SessionState | None:
        if conv_id in self._sessions:
            return self._sessions[conv_id]

        if self._local_keys is None or self._client is None:
            return None

        peer_username = self._peer_usernames.get(peer_id)
        if peer_username is None:
            return None

        try:
            peer_bundle = await self._client.get_keys(peer_username)
        except IMClientError:
            return None
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
            return None

        my_id             = self._user_id or self._username
        peer_identity_pub = base64.b64decode(peer_bundle.identity_pub_b64)
        peer_dh_pub       = base64.b64decode(peer_bundle.dh_pub_b64)

        session_key, eph_pub_bytes, conv_dh_pub_bytes = derive_session_key_as_initiator(
            my_identity_kp=self._local_keys.identity_kp,
            my_dh_kp=self._local_keys.dh_kp,
            peer_identity_pub_bytes=peer_identity_pub,
            peer_dh_pub_bytes=peer_dh_pub,
            my_user_id=my_id,
            peer_user_id=peer_id,
            conversation_id=conv_id,
        )

        send_chain, recv_chain = derive_ratchet_chains(session_key.raw, initiator=True)
        cache = IdentityKeyCache()
        cache.check_and_update(peer_id, peer_identity_pub)

        state = SessionState(
            session_key=session_key,
            send_chain=send_chain,
            recv_chain=recv_chain,
            replay_protector=ReplayProtector(),
            identity_key_cache=cache,
        )
        state._eph_pub_b64 = base64.b64encode(eph_pub_bytes).decode()  # type: ignore[attr-defined]
        state._conv_dh_pub_b64 = base64.b64encode(conv_dh_pub_bytes).decode()  # type: ignore[attr-defined]

        self._sessions[conv_id] = state
        self._persist_sessions()
        return state

    # ------------------------------------------------------------------
    # Cleanup on exit
    # ------------------------------------------------------------------

    async def on_unmount(self) -> None:
        self._persist_sessions()
        if self._client:
            await self._client.__aexit__(None, None, None)
