"""
client/ui/screens/conversations.py — Main screen after login.
FIXED:
1. Compact list spacing to match modern chat apps.
2. Circle avatars for better visual hierarchy.
3. Horizontal name display to prevent truncation of long usernames.
4. Added RequestReload message to support external refresh triggers.
"""

from __future__ import annotations
import structlog

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, ListItem, ListView, Static
from client.ui.contracts import ConversationSummaryViewModel

log = structlog.get_logger()


class ConversationItem(ListItem):
    def __init__(self, summary: ConversationSummaryViewModel) -> None:
        super().__init__()
        self.conv_id = summary.conv_id
        self.peer_id = summary.peer_id
        self.peer_username = summary.peer_username
        self.unread = summary.unread_count
        self.requires_action = summary.requires_action
        self.primary_banner = summary.primary_banner

    def compose(self) -> ComposeResult:
        avatar = self.peer_username[0].upper() if self.peer_username else "?"
        yield Static(avatar, classes="circle")
        with Vertical(classes="text_container"):
            with Horizontal(classes="header_row"):
                yield Static(self.peer_username, classes="conversation_name")
                yield Static(self.render_time_meta(), classes="conversation_meta")
            yield Static(self.render_snippet_line(), classes="conversation_snippet")

    def render_time_meta(self) -> str:
        if self.unread > 0:
            return f"● {self.unread}"
        return "✓✓"

    def render_snippet_line(self) -> str:
        if self.primary_banner is not None:
            return self.primary_banner.title
        return "Delivered. Double check TTL settings in chat."


class ConversationListScreen(Screen):
    """
    Main screen containing the scrollable list of friends/chats.
    """
    CSS = """
    ConversationListScreen {
        layout: vertical;
        background: #0a0a0f;
    }
    #header {
        height: 3;
        background: #0d0d1a;
        border-bottom: solid #00ff9f;
        color: #00ff9f;
        text-style: bold;
        content-align: center middle;
    }
    #conv_list {
        height: 1fr;
        background: #0a0a0f;
        padding: 1 2;
    }
    #conv_list > ListItem {
        height: 4; /* Fixed short distance between names */
        margin-bottom: 1;
        layout: horizontal;
        padding: 0 1;
        background: transparent;
    }
    #conv_list > ListItem:hover { 
        background: #0d0d2a; 
    }

    /* ── Circle Avatar ── */
    .circle {
        width: 5;
        height: 3;
        background: #005c4b;
        color: white;
        content-align: center middle;
        text-style: bold;
        margin-right: 2;
    }

    /* ── Text Layout ── */
    .text_container {
        width: 1fr;
        height: 3;
    }
    .header_row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }

    .conversation_name {
        width: 1fr; /* Allows name to take all available horizontal space */
        height: 1;
        text-style: bold;
        color: #f8fafc;
        content-align: left middle;
    }
    .conversation_meta {
        width: auto; /* Shrinks to fit the unread badge/ticks */
        height: 1;
        color: #00ff9f;
        text-style: italic;
        content-align: right middle;
        padding-left: 1;
    }
    .conversation_snippet {
        color: #94a3b8;
        height: 1;
        text-opacity: 80%;
        content-align: left middle;
    }

    /* ── Bottom Toolbar ── */
    #toolbar {
        height: 3;
        background: #0d0d1a;
        border-top: solid #1a1a3e;
    }
    #toolbar Button {
        background: #0d0d1a;
        color: #00ccff;
        border: tall #1a1a3e;
    }
    #btn_exit { color: #ff4444; border: tall #ff4444; }
    """

    class ConversationSelected(Message):
        def __init__(self, conv_id: str, peer_id: str, peer_username: str) -> None:
            super().__init__()
            self.conv_id = conv_id
            self.peer_id = peer_id
            self.peer_username = peer_username

    class OpenFriends(Message): pass
    class Logout(Message): pass
    class RequestReload(Message): pass  # NEW

    def __init__(self, my_username: str) -> None:
        super().__init__()
        self._my_username = my_username

    def compose(self) -> ComposeResult:
        yield Static(f"◈ SECURE IM  ·  {self._my_username}  ·  🔒 E2EE", id="header")
        yield ListView(id="conv_list")
        with Horizontal(id="toolbar"):
            yield Button("⊕ Friends", variant="primary", id="btn_friends")
            yield Button("⏻ Logout", variant="default", id="btn_logout")
            yield Button("✕ Exit", variant="default", id="btn_exit")

    def populate(self, conversations: list[ConversationSummaryViewModel]) -> None:
        lv = self.query_one("#conv_list", ListView)
        lv.clear()
        for c in conversations:
            lv.append(ConversationItem(c))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ConversationItem):
            self.post_message(self.ConversationSelected(
                event.item.conv_id, event.item.peer_id, event.item.peer_username
            ))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_friends":
            self.post_message(self.OpenFriends())
        elif event.button.id == "btn_logout":
            self.post_message(self.Logout())
        elif event.button.id == "btn_exit":
            self.app.exit()

    async def on_request_reload(self, _: RequestReload) -> None:
        """Handles external reload requests (e.g. after accepting a friend).

        Defensive: supports both async and sync app refresh implementations,
        and logs failures without raising to the message dispatch loop.
        """
        load_fn = getattr(self.app, "load_conversations", None)
        if load_fn is None:
            return

        try:
            result = load_fn()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            log.warning("conversation_reload_failed", err=str(exc))
