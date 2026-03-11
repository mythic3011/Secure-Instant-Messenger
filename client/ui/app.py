"""
client/ui/app.py — Main Textual TUI application.

Wires together all screens, IMClient, crypto, and local storage.
Handles all message events from screens and dispatches WebSocket pushes.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import structlog
from textual.app import App, ComposeResult
from textual.widgets import Static

from client.api.client import IMClient, IMClientError
import httpx
from client.crypto.session import (
    DHKeypair,
    IdentityKeyCache,
    IdentityKeypair,
    KeyChangeWarning,
    ReplayProtector,
    build_and_encrypt,
    compute_fingerprint,
    decrypt_envelope,
    derive_session_key_as_initiator,
    derive_session_key_as_responder,
    make_key_signature,
)
from client.crypto.storage import (
    LocalKeys,
    SessionState,
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
    sweep_expired,
    upsert_conversation,
    update_delivery_status,
)
from client.ui.screens.conversations import ConversationListScreen
from client.ui.screens.login import LoginScreen
from shared.protocol import (
    DeliveryAck,
    LoginRequest,
    MessageEnvelope,
    RegisterRequest,
)

log = structlog.get_logger()


class IMApp(App):
    """
    Main application. Lifecycle:
      1. Show LoginScreen
      2. On login: load keystore, connect WS, show ConversationListScreen
      3. On conversation select: push ChatScreen
    """

    TITLE = "COMP3334 Secure IM"

    def __init__(self, server_url: str, username: str) -> None:
        super().__init__()
        self._server_url = server_url
        self._username   = username
        self._client: IMClient | None = None
        self._local_keys: LocalKeys | None = None
        self._password: str | None = None
        self._user_id: str | None = None
        # Per-conversation state: conv_id → SessionState
        self._sessions: dict[str, SessionState] = {}
        # Per-conversation TTL setting
        self._ttl_settings: dict[str, int | None] = {}
        # Per-conversation send counter
        self._counters: dict[str, int] = {}
        # peer_id → username cache (for get_keys lookups)
        self._peer_usernames: dict[str, str] = {}

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

        self._client = IMClient(self._server_url)
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
            login_screen.query_one("#error", Static).update(f"Cannot reach server — is it running? ({type(e).__name__})")
            await self._client.__aexit__(None, None, None)
            self._client = None
            return
        except Exception as e:
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

        client = IMClient(self._server_url)
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
                except Exception:
                    pass
                return
            except IMClientError as e:
                try:
                    self.screen.query_one("#error", Static).update(
                        f"Registration failed: {e.detail}"
                    )
                except Exception:
                    pass
                return
            except Exception as e:
                try:
                    self.screen.query_one("#error", Static).update(
                        f"Unexpected error: {e}"
                    )
                except Exception:
                    pass
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
            except Exception:
                pass
            await self._client.__aexit__(None, None, None)
            self._client = None
        if self._local_keys and self._password and self._username:
            save_sessions(self._username, self._password, self._sessions)
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
        my_id   = self._user_id or self._username
        ttl     = self._ttl_settings.get(conv_id)
        counter = self._counters.get(conv_id, 0)

        # require that the API client exists before proceeding
        if self._client is None:
            return

        session_state = await self._ensure_session(conv_id, peer_id)
        if session_state is None:
            chat.add_message("system", "Could not establish session.", int(time.time()), "sent", None, False)
            return

        sent_at  = int(time.time())
        envelope = build_and_encrypt(
            session_key=session_state.session_key,
            plaintext=msg.text,
            sender_id=my_id,
            recipient_id=peer_id,
            conversation_id=conv_id,
            counter=counter,
            ttl_seconds=ttl,
            sent_at=sent_at,
        )

        # Include eph_pub on the first message so Bob can derive the session key
        if counter == 0 and hasattr(session_state, "_eph_pub_b64"):
            envelope.eph_pub_b64 = session_state._eph_pub_b64  # type: ignore[attr-defined]

        try:
            await self._client.send_message(envelope)
        except IMClientError as e:
            chat.add_message("system", f"Send failed: {e.detail}", sent_at, "sent", None, False)
            return

        self._counters[conv_id] = counter + 1

        await save_message(
            id=envelope.id,
            conversation_id=conv_id,
            sender_id=my_id,
            recipient_id=peer_id,
            counter=counter,
            plaintext=msg.text,
            sent_at=sent_at,
            ttl_seconds=ttl,
            delivery_status="sent",
        )

        await upsert_conversation(
            id=conv_id,
            peer_id=peer_id,
            peer_username=self._peer_usernames.get(peer_id, peer_id),
            last_message_at=sent_at,
            unread_count=0,
        )

        chat.add_message(my_id, msg.text, sent_at, "sent", ttl, is_mine=True)

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
                screen = self.query_one(FriendsScreen)
                screen.populate_pending([r.model_dump() for r in requests])
            except Exception:
                pass
        except Exception:
            pass

    async def on_friends_screen_send_request(self, msg: Any) -> None:
        from client.ui.screens.friends import FriendsScreen
        if self._client is None:
            return
        try:
            screen = self.query_one(FriendsScreen)
        except Exception:
            return
        try:
            await self._client.send_friend_request(msg.username)
            screen.show_status(f"Request sent to {msg.username}.")
        except IMClientError as e:
            screen.show_error(f"Failed: {e.detail}")

    async def on_friends_screen_accept_request(self, msg: Any) -> None:
        if self._client is None:
            return
        try:
            await self._client.handle_friend_request(msg.request_id, "accept")
        except Exception:
            pass

    async def on_friends_screen_decline_request(self, msg: Any) -> None:
        if self._client is None:
            return
        try:
            await self._client.handle_friend_request(msg.request_id, "decline")
        except Exception:
            pass

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

    async def on_settings_screen_mark_verified(self, msg: Any) -> None:
        from client.ui.screens.chat import ChatScreen
        stack = self.screen_stack
        chat = stack[-2] if len(stack) >= 2 else None
        if isinstance(chat, ChatScreen):
            state = self._sessions.get(chat.conversation_id)
            if state:
                peer_pub = state.identity_key_cache.get(chat.peer_id)
                if peer_pub:
                    state.identity_key_cache.mark_verified(chat.peer_id, peer_pub)
                    try:
                        chat.query_one("#warning", Static).update("")
                    except Exception:
                        pass

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
        except Exception:
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
            if envelope.eph_pub_b64 is None or self._local_keys is None:
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
            except Exception as e:
                log.warning("get_keys failed", peer=peer_username, err=str(e))
                return

            peer_identity_pub = base64.b64decode(peer_bundle.identity_pub_b64)
            peer_dh_pub       = base64.b64decode(peer_bundle.dh_pub_b64)
            eph_pub           = base64.b64decode(envelope.eph_pub_b64)

            session_key = derive_session_key_as_responder(
                my_identity_kp=self._local_keys.identity_kp,
                my_dh_kp=self._local_keys.dh_kp,
                peer_identity_pub_bytes=peer_identity_pub,
                peer_dh_pub_bytes=peer_dh_pub,
                eph_pub_bytes=eph_pub,
                my_user_id=my_id,
                peer_user_id=peer_id,
                conversation_id=conv_id,
            )
            cache = IdentityKeyCache()
            cache.check_and_update(peer_id, peer_identity_pub)
            state = SessionState(
                session_key=session_key,
                replay_protector=ReplayProtector(),
                identity_key_cache=cache,
            )
            self._sessions[conv_id] = state

        # Key change check on re-keying messages
        key_changed = False
        if envelope.eph_pub_b64:
            peer_username = self._peer_usernames.get(peer_id)
            client = self._client
            if peer_username and client:
                try:
                    peer_bundle = await client.get_keys(peer_username)
                    peer_identity_pub = base64.b64decode(peer_bundle.identity_pub_b64)
                    state.identity_key_cache.check_and_update(peer_id, peer_identity_pub)
                except KeyChangeWarning:
                    key_changed = True
                except Exception:
                    pass

        # Decrypt — drop silently on replay or tamper
        try:
            plaintext = decrypt_envelope(
                session_key=state.session_key,
                envelope=envelope,
                replay_protector=state.replay_protector,
            )
        except Exception:
            return

        # Persist locally
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

        peer_username = self._peer_usernames.get(peer_id, peer_id)
        await upsert_conversation(
            id=conv_id,
            peer_id=peer_id,
            peer_username=peer_username,
            last_message_at=envelope.sent_at,
            unread_count=0,  # will be incremented below if not active
        )

        # Route to active chat or increment unread
        from client.ui.screens.chat import ChatScreen
        active_chat = self.screen if isinstance(self.screen, ChatScreen) else None
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
                await self._client.send_ack(DeliveryAck(
                    message_id=envelope.id,
                    conversation_id=conv_id,
                    ack_nonce_b64="",
                    ack_ct_b64="",
                ))
            except Exception:
                pass

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
        except (IMClientError, httpx.ConnectError, httpx.TimeoutException,
                httpx.NetworkError, Exception):
            return None

        my_id             = self._user_id or self._username
        peer_identity_pub = base64.b64decode(peer_bundle.identity_pub_b64)
        peer_dh_pub       = base64.b64decode(peer_bundle.dh_pub_b64)

        session_key, eph_pub_bytes = derive_session_key_as_initiator(
            my_identity_kp=self._local_keys.identity_kp,
            my_dh_kp=self._local_keys.dh_kp,
            peer_identity_pub_bytes=peer_identity_pub,
            peer_dh_pub_bytes=peer_dh_pub,
            my_user_id=my_id,
            peer_user_id=peer_id,
            conversation_id=conv_id,
        )

        cache = IdentityKeyCache()
        cache.check_and_update(peer_id, peer_identity_pub)

        state = SessionState(
            session_key=session_key,
            replay_protector=ReplayProtector(),
            identity_key_cache=cache,
        )
        state._eph_pub_b64 = base64.b64encode(eph_pub_bytes).decode()  # type: ignore[attr-defined]

        self._sessions[conv_id] = state
        return state

    # ------------------------------------------------------------------
    # Cleanup on exit
    # ------------------------------------------------------------------

    async def on_unmount(self) -> None:
        if self._local_keys and self._password and self._username:
            save_sessions(self._username, self._password, self._sessions)
        if self._client:
            await self._client.__aexit__(None, None, None)
