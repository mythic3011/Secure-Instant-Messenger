"""
client/ui/screens/conversations.py — Main screen after login.
Shows conversation list ordered by last activity (R23) with unread counters (R24).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, ListItem, ListView, Static

from client.ui.contracts import ConversationSummaryViewModel, UIBanner


class ConversationItem(ListItem):
    def __init__(self, summary: ConversationSummaryViewModel) -> None:
        super().__init__()
        self.conv_id = summary.conv_id
        self.peer_id = summary.peer_id
        self.peer_username = summary.peer_username
        self.unread = summary.unread_count
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
        background: $surface;
    }
    #header {
        height: 4;
        background: #0d1324;
        border-bottom: solid #14304f;
        color: #7dd3fc;
        text-style: bold;
        content-align: left middle;
        padding: 0 2 0 3;
    }
    #hero {
        height: 4;
        margin: 1 2 1 2;
        padding: 0 2;
        background: #101826;
        border: round #14304f;
    }
    #hero_title {
        color: #f8fafc;
        text-style: bold;
    }
    #hero_subtitle {
        color: #94a3b8;
    }
    #summary {
        color: #86efac;
        text-style: bold;
    }
    #conv_list {
        height: 1fr;
        margin: 0 2;
        border: solid #1a2742;
        background: #0b1120;
    }
    #conv_list > ListItem {
        color: #dbeafe;
        padding: 1 2;
    }
    #conv_list > ListItem:hover { background: #111c33; }
    #conv_list > ListItem.--highlight {
        background: #12233b;
        border-left: thick #38bdf8;
    }
    #conv_list > ListItem.requires-action {
        border-left: thick #f97316;
        background: #27171a;
    }
    #conv_list > ListItem.requires-action.--highlight {
        background: #311b20;
        border-left: thick #f97316;
    }
    #conv_list > ListItem.has-unread {
        border-left: thick #22c55e;
    }
    .conversation_name {
        color: #f8fafc;
        text-style: bold;
    }
    .conversation_status {
        color: #93c5fd;
    }
    #conv_list > ListItem.requires-action .conversation_status {
        color: #fdba74;
        text-style: bold;
    }
    .conversation_detail {
        color: #94a3b8;
    }
    #conv_list > ListItem.requires-action .conversation_detail {
        color: #fed7aa;
    }
    #empty_state {
        height: 3;
        margin: 0 2 1 2;
        color: #64748b;
        content-align: center middle;
    }
    #toolbar {
        height: 3;
        margin-top: 1;
        background: #0d1324;
        border-top: solid #14304f;
    }
    #toolbar Button {
        background: #0d1324;
        color: #7dd3fc;
        border: tall #1a2742;
    }
    #toolbar Button:hover { background: #111c33; color: #86efac; }
    #btn_exit {
        color: #f87171;
        border: tall #7f1d1d;
    }
    #btn_exit:hover { background: #2b1113; color: #fca5a5; }
    """

    class ConversationSelected(Message):
        def __init__(self, conv_id: str, peer_id: str, peer_username: str) -> None:
            super().__init__()
            self.conv_id = conv_id
            self.peer_id = peer_id
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
        yield Static(f"◈ SECURE IM  ·  {self._my_username}  ·  protected inbox", id="header")
        with Vertical(id="hero"):
            yield Static("Conversation overview", id="hero_title")
            yield Static(
                "Accepted friends appear here immediately and stay ordered by activity.",
                id="hero_subtitle",
            )
            yield Static("No conversations yet", id="summary")
        yield ListView(id="conv_list")
        yield Static(self.render_empty_state(), id="empty_state")
        with Horizontal(id="toolbar"):
            yield Button("⊕ Friends", variant="primary", id="btn_friends")
            yield Button("⏻ Logout", variant="default", id="btn_logout")
            yield Button("✕ Exit", variant="default", id="btn_exit")

    @staticmethod
    def _pluralize(value: int, singular: str, plural: str | None = None) -> str:
        noun = singular if value == 1 else (plural or f"{singular}s")
        return f"{value} {noun}"

    def render_summary_line(self, conversations: list[ConversationSummaryViewModel]) -> str:
        conversation_count = len(conversations)
        unread_count = sum(item.unread_count for item in conversations)
        requires_review_count = sum(1 for item in conversations if item.requires_action)
        return (
            f"{self._pluralize(conversation_count, 'conversation')}  ·  "
            f"{self._pluralize(unread_count, 'unread', 'unread')}  ·  "
            f"{self._pluralize(requires_review_count, 'needs review', 'needs review')}"
        )

    @staticmethod
    def render_empty_state() -> str:
        return "No conversations yet.\nAccepted friends will appear here immediately."

    def _update_overview(self, conversations: list[ConversationSummaryViewModel]) -> None:
        summary = (
            self.render_summary_line(conversations) if conversations else "No conversations yet"
        )
        empty_state = "" if conversations else self.render_empty_state()
        try:
            self.query_one("#summary", Static).update(summary)
            self.query_one("#empty_state", Static).update(empty_state)
        except NoMatches:
            return

    def populate(self, conversations: list[ConversationSummaryViewModel]) -> None:
        """Fill the list from controller-provided summary view models."""
        lv = self.query_one("#conv_list", ListView)
        lv.clear()
        self._items.clear()
        for c in conversations:
            item = ConversationItem(c)
            self._items[c.conv_id] = item
            lv.append(item)
        self._update_overview(conversations)

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
            self._update_overview(
                [
                    ConversationSummaryViewModel(
                        conv_id=current.conv_id,
                        peer_id=current.peer_id,
                        peer_username=current.peer_username,
                        unread_count=current.unread,
                        requires_action=current.requires_action,
                        primary_banner=current.primary_banner,
                    )
                    for current in self._items.values()
                ]
            )

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
