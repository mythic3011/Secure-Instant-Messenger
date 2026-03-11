"""
client/ui/screens/conversations.py — Main screen after login.
Shows conversation list ordered by last activity (R23) with unread counters (R24).
"""

from __future__ import annotations

from textual.message import Message
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, ListItem, ListView, Static


class ConversationItem(ListItem):
    def __init__(self, conv_id: str, peer_id: str, peer_username: str, unread: int) -> None:
        super().__init__()
        self.conv_id       = conv_id
        self.peer_id       = peer_id
        self.peer_username = peer_username
        self.unread        = unread

    def compose(self) -> ComposeResult:
        badge = f"  [{self.unread}]" if self.unread > 0 else ""
        yield Static(f"  {self.peer_username}{badge}")


class ConversationListScreen(Screen):
    """
    Main screen: list of conversations + Friends / Logout buttons.
    """

    CSS = """
    ConversationListScreen { layout: vertical; }
    #conv_list { height: 1fr; border: solid $primary; }
    #toolbar   { height: 3; }
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
        yield Header()
        yield ListView(id="conv_list")
        with Horizontal(id="toolbar"):
            yield Button("Friends", variant="primary", id="btn_friends")
            yield Button("Logout",  variant="default", id="btn_logout")
        yield Footer()

    def on_mount(self) -> None:
        self.title = f"Conversations — {self._my_username}"

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
                f"  {item.peer_username}  [{unread_count}]" if unread_count > 0
                else f"  {item.peer_username}"
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
