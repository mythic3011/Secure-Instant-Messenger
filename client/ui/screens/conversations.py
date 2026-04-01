"""
client/ui/screens/conversations.py — Main screen after login.
Shows conversation list ordered by last activity (R23) with unread counters (R24).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, ListItem, ListView, Static

from client.ui.contracts import ConversationSummaryViewModel, UIBanner


class ConversationItem(ListItem):
    def __init__(self, summary: ConversationSummaryViewModel) -> None:
        super().__init__()
        self.conv_id       = summary.conv_id
        self.peer_id       = summary.peer_id
        self.peer_username = summary.peer_username
        self.unread        = summary.unread_count
        self.requires_action = summary.requires_action
        self.primary_banner = summary.primary_banner

    def on_mount(self) -> None:
        if self.requires_action:
            self.add_class("requires-action")
        elif self.unread > 0:
            self.add_class("has-unread")

    def render_primary_line(self) -> str:
        return self.peer_username

    def render_status_line(self) -> str:
        parts: list[str] = []
        if self.requires_action:
            parts.append("Security review required")
        if self.unread > 0:
            parts.append(f"{self.unread} unread")
        if not parts:
            return "No unread messages"
        return "  •  ".join(parts)

    def render_secondary_line(self) -> str:
        if self.primary_banner is not None:
            return self.primary_banner.title
        return "No security actions pending"

    def compose(self) -> ComposeResult:
        with Vertical(classes="conversation_row"):
            yield Static(self.render_primary_line(), classes="conversation_name")
            yield Static(self.render_status_line(), classes="conversation_status")
            yield Static(self.render_secondary_line(), classes="conversation_detail")


class ConversationListScreen(Screen):
    """
    Main screen: list of conversations + Friends / Logout buttons.
    """

    CSS = """
    ConversationListScreen {
        layout: vertical;
        background: #0a0a0f;
    }
    #btn_exit {
        color: #ff4444;
        border: tall #ff4444;
    }
    #btn_exit:hover { background: #1a0000; color: #ff6666; }
    #header {
        height: 3;
        background: #0d0d1a;
        border-bottom: solid #00ff9f;
        color: #00ff9f;
        text-style: bold;
        content-align: center middle;
        padding: 0 2;
    }
    #conv_list {
        height: 1fr;
        border: solid #1a1a3e;
        background: #0a0a0f;
    }
    #conv_list > ListItem {
        color: #c0c0e0;
        padding: 1 2;
    }
    #conv_list > ListItem:hover { background: #0d0d2a; }
    #conv_list > ListItem.--highlight {
        background: #0d1a2a;
        border-left: thick #38bdf8;
    }
    #conv_list > ListItem.requires-action {
        border-left: thick #ef4444;
        background: #1c1015;
    }
    #conv_list > ListItem.requires-action.--highlight {
        background: #28141c;
        border-left: thick #f97316;
    }
    #conv_list > ListItem.has-unread {
        border-left: thick #00ff9f;
    }
    .conversation_name {
        color: #f8fafc;
        text-style: bold;
    }
    .conversation_status {
        color: #93c5fd;
    }
    #conv_list > ListItem.requires-action .conversation_status {
        color: #fecaca;
        text-style: bold;
    }
    .conversation_detail {
        color: #94a3b8;
    }
    #conv_list > ListItem.requires-action .conversation_detail {
        color: #fca5a5;
    }
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
    #toolbar Button:hover { background: #0d0d2a; color: #00ff9f; }
    """

    class ConversationSelected(Message):
        def __init__(self, conv_id: str, peer_id: str, peer_username: str) -> None:
            super().__init__()
            self.conv_id       = conv_id
            self.peer_id       = peer_id
            self.peer_username = peer_username

    class OpenFriends(Message):
        pass

    class Logout(Message):
        pass

    def __init__(self, my_username: str) -> None:
        super().__init__()
        self._my_username = my_username
        self._items: dict[str, ConversationItem] = {}

    def compose(self) -> ComposeResult:
        yield Static(f"◈ SECURE IM  ·  {self._my_username}  ·  🔒 E2EE", id="header")
        yield ListView(id="conv_list")
        with Horizontal(id="toolbar"):
            yield Button("⊕ Friends", variant="primary", id="btn_friends")
            yield Button("⏻ Logout",  variant="default", id="btn_logout")
            yield Button("✕ Exit",    variant="default", id="btn_exit")

    def populate(self, conversations: list[ConversationSummaryViewModel]) -> None:
        """Fill the list from controller-provided summary view models."""
        lv = self.query_one("#conv_list", ListView)
        lv.clear()
        self._items.clear()
        for c in conversations:
            item = ConversationItem(c)
            self._items[c.conv_id] = item
            lv.append(item)

    def refresh_conversation(
        self,
        conv_id: str,
        unread_count: int,
        *,
        requires_action: bool | None = None,
        primary_banner: UIBanner | None = None,
    ) -> None:
        """Update unread/security summary for a single conversation."""
        item = self._items.get(conv_id)
        if item:
            item.unread = unread_count
            if requires_action is not None:
                item.requires_action = requires_action
            if primary_banner is not None or (requires_action is False):
                item.primary_banner = primary_banner
            item.remove_class("requires-action", "has-unread")
            if item.requires_action:
                item.add_class("requires-action")
            elif item.unread > 0:
                item.add_class("has-unread")
            item.query_one(".conversation_name", Static).update(item.render_primary_line())
            item.query_one(".conversation_status", Static).update(item.render_status_line())
            item.query_one(".conversation_detail", Static).update(item.render_secondary_line())

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, ConversationItem):
            self.post_message(
                self.ConversationSelected(item.conv_id, item.peer_id, item.peer_username)
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_friends":
            self.post_message(self.OpenFriends())
        elif event.button.id == "btn_logout":
            self.post_message(self.Logout())
        elif event.button.id == "btn_exit":
            self.app.exit()
