"""
client/ui/screens/chat.py — Chat view screen.
Shows message history for a conversation and handles send/receive.
Covers: R5 (fingerprint), R10/R11 (TTL display + deletion), R17 (delivery status)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Input, ListItem, ListView, Static

from client.ui.contracts import ChatHistoryResult, SendResult, UIBanner
from client.ui.theme import Theme
from client.ui.widgets.banner import Banner

if TYPE_CHECKING:
    pass


class MessageItem(ListItem):
    """A single message row in the chat list."""

    def __init__(
        self,
        sender: str,
        text: str,
        sent_at: int,
        status: str,
        ttl_seconds: int | None,
        is_mine: bool,
    ) -> None:
        super().__init__()
        self._sender = sender
        self._text = text
        self._sent_at = sent_at
        self._status = status
        self._ttl = ttl_seconds
        self._is_mine = is_mine
        # Pre-compute expiry so _sweep_ttl() can compare without re-parsing
        self._expires_at: int | None = (sent_at + ttl_seconds) if ttl_seconds else None

    def on_mount(self) -> None:
        self.add_class("mine" if self._is_mine else "theirs")

    def compose(self) -> ComposeResult:
        import datetime

        ts = datetime.datetime.fromtimestamp(self._sent_at).strftime("%H:%M")
        # READ status is reserved for future UX/protocol extension and not currently implemented.
        # For now, treat "read" identically to "delivered" in the UI.
        status_icon = {"sent": "✓", "delivered": "✓✓", "read": "✓✓"}.get(self._status, "")

        # Show live countdown instead of raw TTL seconds (R10)
        if self._expires_at is not None:
            remaining = self._expires_at - int(time.time())
            if remaining <= 0:
                ttl_tag = "TTL expired"
            else:
                ttl_tag = f"TTL {remaining}s"
        else:
            ttl_tag = "TTL off"

        meta_parts = [ts]
        if self._is_mine and status_icon:
            meta_parts.append(status_icon)
        meta_parts.append(ttl_tag)
        meta_line = "  ·  ".join(meta_parts)

        with Vertical():
            if not self._is_mine:
                yield Static(f"{self._sender}", classes="message_sender")
            yield Static(self._text, classes="message_body")
            yield Static(meta_line, classes="message_meta")


class PlaceholderItem(ListItem):
    def __init__(self, title: str, detail: str) -> None:
        super().__init__()
        self._title = title
        self._detail = detail

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="placeholder_title")
            yield Static(self._detail, classes="placeholder_detail")


class ChatScreen(Screen):
    """
    Chat view for a single conversation.
    Receives new messages via the app's WebSocket callback.
    """

    CSS = (
        """
    ChatScreen {
        layout: vertical;
        background: __APP_BACKGROUND__;
    }
    #btn_back {
        background: __SURFACE__;
        color: __ERROR__;
        border: tall __ERROR__;
    }
    #btn_back:hover { background: #1a0000; }
    #header {
        height: 3;
        background: __SURFACE__;
        border-bottom: solid __ACCENT__;
        color: __ACCENT__;
        text-style: bold;
        content-align: left middle;
        padding: 0 2;
    }
    #messages {
        height: 1fr;
        border: solid #1a1a3e;
        background: __APP_BACKGROUND__;
        padding: 0 1;
    }
    /* theirs — left aligned, white */
    #messages > ListItem.theirs {
        color: #c0c0e0;
        background: #0d0d1a;
        border-left: thick #444466;
        margin: 0 8 0 0;
        padding: 0 1;
    }
    /* mine — right aligned, cyan */
    #messages > ListItem.mine {
        color: #00ff9f;
        background: #001a10;
        border-right: thick #00ff9f;
        margin: 0 0 0 8;
        padding: 0 1;
        text-align: right;
    }
    .message_sender {
        color: #9aa0c8;
        text-style: bold;
    }
    .message_body {
        color: #f4f7ff;
        text-style: bold;
    }
    .message_meta {
        color: #7d86ad;
    }
    #warning {
        margin: 0 0 1 0;
    }
    #messages > ListItem.placeholder {
        color: #cbd5e1;
        background: #0d1220;
        border: dashed #334155;
        margin: 1 6;
        padding: 1 2;
    }
    .placeholder_title {
        color: #e2e8f0;
        text-style: bold;
    }
    .placeholder_detail {
        color: #94a3b8;
    }
    #input_row {
        height: 3;
        background: __SURFACE__;
        border-top: solid #1a1a3e;
    }
    #msg_input {
        width: 1fr;
        border: tall #1a1a3e;
        background: __SURFACE_ALT__;
        color: #e0e0ff;
    }
    #msg_input:focus { border: tall __ACCENT__; }
    #btn_send {
        background: __ACCENT__;
        color: #000000;
        text-style: bold;
    }
    #btn_settings {
        background: __SURFACE__;
        color: __ACCENT_ALT__;
        border: tall #1a1a3e;
    }
    """.replace("__APP_BACKGROUND__", Theme.APP_BACKGROUND)
        .replace("__SURFACE__", Theme.SURFACE)
        .replace("__ERROR__", Theme.ERROR)
        .replace("__ACCENT__", Theme.ACCENT)
        .replace("__SURFACE_ALT__", Theme.SURFACE_ALT)
        .replace("__ACCENT_ALT__", Theme.ACCENT_ALT)
    )

    class SendMessage(Message):
        def __init__(self, text: str, ttl: int | None) -> None:
            super().__init__()
            self.text = text
            self.ttl = ttl

    class RequestHistory(Message):
        def __init__(self, conversation_id: str) -> None:
            super().__init__()
            self.conversation_id = conversation_id

    def __init__(
        self,
        conversation_id: str,
        peer_id: str,
        peer_username: str,
        my_user_id: str,
    ) -> None:
        super().__init__()
        self.conversation_id = conversation_id
        self.peer_id = peer_id
        self.peer_username = peer_username
        self.my_user_id = my_user_id
        self._banner: UIBanner | None = None

    def _render_placeholder(self, state_kind: str) -> PlaceholderItem:
        if state_kind == "degraded":
            item = PlaceholderItem(
                "History unavailable",
                "Local message history cannot be shown on this device right now.",
            )
        else:
            item = PlaceholderItem(
                "No messages yet",
                "Start the conversation here. Security warnings will still appear above.",
            )
        item.add_class("placeholder")
        return item

    def _set_send_enabled(self, enabled: bool) -> None:
        self.query_one("#btn_send", Button).disabled = not enabled

    def compose(self) -> ComposeResult:
        yield Static(f"◈ {self.peer_username}  ·  🔒 E2EE", id="header")
        yield Banner(id="warning")
        yield ListView(id="messages")
        with Horizontal(id="input_row"):
            yield Input(placeholder="Type a message…", id="msg_input")
            yield Button("Send", variant="primary", id="btn_send")
            yield Button("⚙", id="btn_settings", tooltip="Fingerprint / TTL")
            yield Button("✕", id="btn_back", tooltip="Back")

    def on_mount(self) -> None:
        self.title = f"Chat — {self.peer_username}"
        self.app.call_later(self._request_history)
        # R11: sweep expired messages every 30 s while chat is open
        self._ttl_timer = self.set_interval(30, self._sweep_ttl)

    async def _request_history(self) -> None:
        self.post_message(self.RequestHistory(self.conversation_id))

    def _refresh_warning(self) -> None:
        warning = self.query_one("#warning")
        if isinstance(warning, Banner):
            warning.show_banner(self._banner)
            return
        cast(Any, warning).update(
            "" if self._banner is None else f"{self._banner.title}\n{self._banner.message}"
        )

    def set_banner(self, banner: UIBanner | None) -> None:
        self._banner = banner
        self._refresh_warning()

    def render_history_result(self, result: ChatHistoryResult) -> None:
        lv = self.query_one("#messages", ListView)
        lv.clear()
        if result.messages:
            for m in reversed(result.messages):
                lv.append(
                    MessageItem(
                        sender=m["sender_id"],
                        text=m["plaintext"],
                        sent_at=m["sent_at"],
                        status=m["delivery_status"],
                        ttl_seconds=m["ttl_seconds"],
                        is_mine=(m["sender_id"] == self.my_user_id),
                    )
                )
        else:
            lv.append(self._render_placeholder(result.state.kind))
        self.set_banner(result.state.primary_banner)
        self._set_send_enabled("send" not in result.state.disabled_actions)
        lv.scroll_end(animate=False)

    def apply_send_result(self, result: SendResult) -> None:
        self.set_banner(result.state.primary_banner)
        self._set_send_enabled("send" not in result.state.disabled_actions)
        if result.ok and "send" not in result.state.disabled_actions:
            self.query_one("#msg_input", Input).value = ""

    async def _sweep_ttl(self) -> None:
        """
        R11: Remove expired TTL messages from the UI and local storage.
        Called every 30 s by the interval timer set in on_mount().
        Also called on startup via app.py sweep_expired() (local DB layer).
        """
        from client.state.store import sweep_expired

        # Purge expired rows from local SQLite first
        await sweep_expired()

        # Remove expired MessageItems from the visible ListView
        now = int(time.time())
        lv = self.query_one("#messages", ListView)
        expired = [
            item
            for item in lv.children
            if isinstance(item, MessageItem)
            and item._expires_at is not None
            and item._expires_at <= now
        ]
        for item in expired:
            await item.remove()

    def show_key_warning(self, peer_username: str) -> None:
        """Display key change warning (R6)."""
        self.set_banner(
            UIBanner(
                code="key_changed",
                severity="error",
                title="Identity key changed",
                message=(
                    f"{peer_username}'s identity key has changed. "
                    "Verify fingerprint in settings before trusting new messages."
                ),
                persistent=True,
                dismissible=False,
            )
        )

    def clear_key_warning(self) -> None:
        if self._banner and self._banner.code == "key_changed":
            self.set_banner(None)

    def add_message(
        self,
        sender: str,
        text: str,
        sent_at: int,
        status: str,
        ttl_seconds: int | None,
        is_mine: bool,
    ) -> None:
        lv = self.query_one("#messages", ListView)
        lv.append(MessageItem(sender, text, sent_at, status, ttl_seconds, is_mine))
        lv.scroll_end(animate=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_send":
            self._send()
        elif event.button.id == "btn_settings":
            from client.ui.screens.settings import SettingsScreen

            self.app.push_screen(
                SettingsScreen(
                    conversation_id=self.conversation_id,
                    peer_username=self.peer_username,
                )
            )
        elif event.button.id == "btn_back":
            self.app.pop_screen()

    def on_key(self, event) -> None:
        if event.key == "ctrl+a":
            inp = self.focused
            if isinstance(inp, Input):
                inp.action_select_all()
                event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "msg_input":
            self._send()

    def _send(self) -> None:
        inp = self.query_one("#msg_input", Input)
        text = inp.value.strip()
        if not text:
            return
        # TTL is managed by app._ttl_settings; pass None here,
        # app.on_chat_screen_send_message reads it from its own state.
        self.post_message(self.SendMessage(text, ttl=None))
