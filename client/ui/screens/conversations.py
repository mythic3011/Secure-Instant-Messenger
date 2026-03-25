"""
client/ui/screens/conversations.py — Main screen after login.
Shows conversation list ordered by last activity (R23) with unread counters (R24).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, ListItem, ListView, Static


class ConversationItem(ListItem):
    def __init__(
        self, conv_id: str, peer_id: str, peer_username: str, unread: int
    ) -> None:
        super().__init__()
        self.conv_id = conv_id
        self.peer_id = peer_id
        self.peer_username = peer_username
        self.unread = unread

    def compose(self) -> ComposeResult:
        if self.unread > 0:
            yield Static(f"  ◉ {self.peer_username}  [{self.unread} new]")
        else:
            yield Static(f"  ○ {self.peer_username}")


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
        padding: 0 1;
    }
    #conv_list > ListItem:hover { background: #0d0d2a; color: #00ff9f; }
    #conv_list > ListItem.--highlight { background: #0d1a2a; color: #00ff9f; }
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
        yield Static(f"◈ SECURE IM  ·  {self._my_username}  ·  🔒 E2EE", id="header")
        yield ListView(id="conv_list")
        with Horizontal(id="toolbar"):
            yield Button("⊕ Friends", variant="primary", id="btn_friends")
            yield Button("⏻ Logout", variant="default", id="btn_logout")
            yield Button("✕ Exit", variant="default", id="btn_exit")

    def populate(self, conversations: list[dict]) -> None:
        """Fill the list from a list of conversation dicts (from local store)."""
        lv = self.query_one("#conv_list", ListView)
        lv.clear()
        self._items.clear()
        for c in conversations:
            item = ConversationItem(
                conv_id=c["id"],
                peer_id=c["peer_id"],
                peer_username=c["peer_username"],
                unread=c.get("unread_count", 0),
            )
            self._items[c["id"]] = item
            lv.append(item)

    def refresh_conversation(self, conv_id: str, unread_count: int) -> None:
        """Update unread badge for a single conversation."""
        item = self._items.get(conv_id)
        if item:
            item.unread = unread_count
            item.query_one(Static).update(
                f"  {item.peer_username}  [{unread_count}]"
                if unread_count > 0
                else f"  {item.peer_username}"
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, ConversationItem):
            self.post_message(
                self.ConversationSelected(
                    item.conv_id, item.peer_id, item.peer_username
                )
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_friends":
            self.post_message(self.OpenFriends())
        elif event.button.id == "btn_logout":
            self.post_message(self.Logout())
        elif event.button.id == "btn_exit":
            self.app.exit()
