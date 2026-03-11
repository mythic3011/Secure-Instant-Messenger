"""
client/ui/screens/chat.py — Chat view screen.
Shows message history for a conversation and handles send/receive.
Covers: R5 (fingerprint), R10/R11 (TTL display + deletion), R17 (delivery status)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

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
        self._sender     = sender
        self._text       = text
        self._sent_at    = sent_at
        self._status     = status
        self._ttl        = ttl_seconds
        self._is_mine    = is_mine
        # Pre-compute expiry so _sweep_ttl() can compare without re-parsing
        self._expires_at: int | None = (sent_at + ttl_seconds) if ttl_seconds else None

    def compose(self) -> ComposeResult:
        import datetime
        ts = datetime.datetime.fromtimestamp(self._sent_at).strftime("%H:%M")
        prefix = "You" if self._is_mine else self._sender
        status_icon = {"sent": "✓", "delivered": "✓✓", "read": "✓✓"}.get(self._status, "")

        # Show live countdown instead of raw TTL seconds (R10)
        if self._expires_at is not None:
            remaining = self._expires_at - int(time.time())
            if remaining <= 0:
                ttl_tag = " [🔥 expired]"
            else:
                ttl_tag = f" [🔥 {remaining}s]"
        else:
            ttl_tag = ""

        yield Static(f"[{ts}] {prefix}: {self._text}  {status_icon}{ttl_tag}")


class ChatScreen(Screen):
    """
    Chat view for a single conversation.
    Receives new messages via the app's WebSocket callback.
    """

    CSS = """
    ChatScreen { layout: vertical; }
    #messages  { height: 1fr; border: solid $primary; }
    #warning   { color: $warning; height: auto; }
    #input_row { height: 3; }
    #msg_input { width: 1fr; }
    """

    class SendMessage(Message):
        def __init__(self, text: str, ttl: int | None) -> None:
            super().__init__()
            self.text = text
            self.ttl  = ttl

    def __init__(
        self,
        conversation_id: str,
        peer_id: str,
        peer_username: str,
        my_user_id: str,
    ) -> None:
        super().__init__()
        self.conversation_id = conversation_id
        self.peer_id         = peer_id
        self.peer_username   = peer_username
        self.my_user_id      = my_user_id
        self._key_warning    = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="warning")
        yield ListView(id="messages")
        with Horizontal(id="input_row"):
            yield Input(placeholder="Type a message…", id="msg_input")
            yield Button("Send", variant="primary", id="btn_send")
            yield Button("⚙", id="btn_settings", tooltip="Fingerprint / TTL")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Chat — {self.peer_username}"
        self.app.call_later(self._load_history)
        # R11: sweep expired messages every 30 s while chat is open
        self._ttl_timer = self.set_interval(30, self._sweep_ttl)

    async def _load_history(self) -> None:
        from client.state.store import get_messages
        msgs = await get_messages(self.conversation_id, limit=50)
        lv = self.query_one("#messages", ListView)
        for m in reversed(msgs):
            lv.append(MessageItem(
                sender=m["sender_id"],
                text=m["plaintext"],
                sent_at=m["sent_at"],
                status=m["delivery_status"],
                ttl_seconds=m["ttl_seconds"],
                is_mine=(m["sender_id"] == self.my_user_id),
            ))
        lv.scroll_end(animate=False)

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
            item for item in lv.children
            if isinstance(item, MessageItem)
            and item._expires_at is not None
            and item._expires_at <= now
        ]
        for item in expired:
            await item.remove()

    def show_key_warning(self, peer_username: str) -> None:
        """Display key change warning (R6)."""
        self._key_warning = True
        self.query_one("#warning", Static).update(
            f"⚠  {peer_username}'s identity key has changed! "
            "Verify fingerprint in ⚙ before trusting messages."
        )

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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "msg_input":
            self._send()

    def _send(self) -> None:
        inp = self.query_one("#msg_input", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""
        # TTL is managed by app._ttl_settings; pass None here,
        # app.on_chat_screen_send_message reads it from its own state.
        self.post_message(self.SendMessage(text, ttl=None))