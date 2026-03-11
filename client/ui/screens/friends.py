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
    FriendsScreen {
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
    #pending_list {
        height: 1fr;
        border: solid #1a1a3e;
        background: #0a0a0f;
    }
    #pending_list > ListItem { color: #c0c0e0; padding: 0 1; }
    #pending_list > ListItem:hover { background: #0d0d2a; color: #00ff9f; }
    #add_row {
        height: 3;
        background: #0d0d1a;
        border-top: solid #1a1a3e;
    }
    #add_input {
        width: 1fr;
        border: tall #1a1a3e;
        background: #0a0a1a;
        color: #e0e0ff;
    }
    #add_input:focus { border: tall #00ccff; }
    #btn_add {
        background: #00ccff;
        color: #000000;
        text-style: bold;
    }
    #btn_back {
        background: #0d0d1a;
        color: #00ccff;
        border: tall #1a1a3e;
    }
    #status { color: #00ff9f; height: auto; padding: 0 1; }
    #error  { color: #ff4444; height: auto; padding: 0 1; text-style: bold; }
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
