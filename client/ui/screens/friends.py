"""
client/ui/screens/friends.py — Friend request management screen.
Covers: R13, R14, R15
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static


class FriendRequestItem(ListItem):
    def __init__(self, request_id: str, sender_name: str) -> None:
        super().__init__()
        self.request_id  = request_id
        self.sender_name = sender_name

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(f"  {self.sender_name}", classes="name")
            yield Button("Accept", variant="success", id=f"accept_{self.request_id}")
            yield Button("Decline", variant="error",  id=f"decline_{self.request_id}")


class FriendsScreen(Screen):
    """
    Friends management: send requests, view pending, accept/decline.
    """

    CSS = """
    FriendsScreen { layout: vertical; }
    #pending_list { height: 1fr; border: solid $primary; }
    #add_row { height: 3; }
    #add_input { width: 1fr; }
    #status { color: $success; height: auto; }
    #error  { color: $error;   height: auto; }
    """

    class SendRequest(Message):
        def __init__(self, username: str) -> None:
            super().__init__()
            self.username = username

    class AcceptRequest(Message):
        def __init__(self, request_id: str) -> None:
            super().__init__()
            self.request_id = request_id

    class DeclineRequest(Message):
        def __init__(self, request_id: str) -> None:
            super().__init__()
            self.request_id = request_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Pending friend requests:", id="pending_label")
        yield ListView(id="pending_list")
        yield Static("", id="status")
        yield Static("", id="error")
        with Horizontal(id="add_row"):
            yield Input(placeholder="Username to add…", id="add_input")
            yield Button("Send Request", variant="primary", id="btn_add")
        yield Button("← Back", id="btn_back")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Friends"
        self.app.call_later(self._load_pending)

    async def _load_pending(self) -> None:
        self.post_message(self._LoadPending())

    class _LoadPending(Message):
        pass

    def populate_pending(self, requests: list[dict]) -> None:
        lv = self.query_one("#pending_list", ListView)
        lv.clear()
        for r in requests:
            lv.append(FriendRequestItem(r["id"], r["sender_name"]))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "btn_add":
            username = self.query_one("#add_input", Input).value.strip()
            if username:
                self.query_one("#add_input", Input).value = ""
                self.post_message(self.SendRequest(username))
        elif btn_id == "btn_back":
            self.app.pop_screen()
        elif btn_id.startswith("accept_"):
            self.post_message(self.AcceptRequest(btn_id.removeprefix("accept_")))
        elif btn_id.startswith("decline_"):
            self.post_message(self.DeclineRequest(btn_id.removeprefix("decline_")))

    def show_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)
        self.query_one("#error",  Static).update("")

    def show_error(self, msg: str) -> None:
        self.query_one("#error",  Static).update(msg)
        self.query_one("#status", Static).update("")
