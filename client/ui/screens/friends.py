"""
client/ui/screens/friends.py — Friend request management screen.
Covers: R13, R14, R15
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Input, ListItem, ListView, Static


class FriendRequestItem(ListItem):
    def __init__(self, request_id: str, sender_name: str) -> None:
        super().__init__()
        self.request_id = request_id
        self.sender_name = sender_name

    def compose(self) -> ComposeResult:
        with Horizontal(classes="req_row"):
            yield Static(f"  ⊛ {self.sender_name}", classes="req_name")
            yield Button(
                "✓ Accept",
                variant="success",
                id=f"accept_{self.request_id}",
                classes="req_btn",
            )
            yield Button(
                "✕ Decline",
                variant="error",
                id=f"decline_{self.request_id}",
                classes="req_btn",
            )


class FriendsScreen(Screen):
    """
    Friends management: send requests, view pending, accept/decline.
    """

    CSS = """
    FriendsScreen {
        layout: vertical;
        background: #0b1120;
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
        margin: 1 2;
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
    #pending_summary {
        color: #86efac;
        text-style: bold;
    }
    #pending_list {
        height: 1fr;
        margin: 0 2;
        border: solid #1a2742;
        background: #0b1120;
    }
    #pending_list > ListItem {
        color: #dbeafe;
        padding: 0 0;
        height: 3;
    }
    #pending_list > ListItem:hover { background: #111c33; }
    .req_row  { height: 3; align: left middle; }
    .req_name { width: 1fr; color: #f8fafc; content-align: left middle; padding: 0 1; }
    .req_btn  { width: 12; height: 3; }
    #pending_empty {
        height: 3;
        margin: 0 2;
        color: #64748b;
        content-align: center middle;
    }
    #status {
        color: #86efac;
        height: 1;
        padding: 0 2;
        content-align: left middle;
    }
    #error  {
        color: #fca5a5;
        height: 1;
        padding: 0 2;
        content-align: left middle;
        text-style: bold;
    }
    #add_row {
        height: 3;
        margin: 1 2 0 2;
        background: #0d1324;
        border: round #1a2742;
    }
    #add_input {
        width: 1fr;
        border: tall #1a2742;
        background: #0f172a;
        color: #f8fafc;
    }
    #add_input:focus { border: tall #38bdf8; }
    #btn_add {
        width: 16;
        background: #38bdf8;
        color: #082f49;
        text-style: bold;
    }
    #btn_add:hover { background: #7dd3fc; }
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
        yield Static("◈ SECURE IM  ·  Friends  ·  trusted contacts", id="header")
        with Vertical(id="hero"):
            yield Static("Friend requests", id="hero_title")
            yield Static(
                "Incoming requests appear here as soon as the server pushes them.",
                id="hero_subtitle",
            )
            yield Static("No incoming requests right now", id="pending_summary")
        yield ListView(id="pending_list")
        yield Static(self.render_empty_state(), id="pending_empty")
        yield Static("", id="status")
        yield Static("", id="error")
        with Horizontal(id="add_row"):
            yield Input(placeholder="Username to add…", id="add_input")
            yield Button("⊕ Send Request", id="btn_add")
        with Horizontal(id="toolbar"):
            yield Button("← Back", id="btn_back")
            yield Button("✕ Exit", id="btn_exit")

    def on_mount(self) -> None:
        self.query_one("#add_input", Input).focus()
        self.app.call_later(self._load_pending)

    async def _load_pending(self) -> None:
        self.post_message(self._LoadPending())

    class _LoadPending(Message):
        pass

    @staticmethod
    def _pluralize(value: int, singular: str, plural: str | None = None) -> str:
        noun = singular if value == 1 else (plural or f"{singular}s")
        return f"{value} {noun}"

    def render_pending_summary(self, requests: list[dict]) -> str:
        count = len(requests)
        return f"{self._pluralize(count, 'incoming request')}  ·  respond to start chatting"

    @staticmethod
    def render_empty_state() -> str:
        return "No pending requests.\nSend an invite below to start a secure conversation."

    def populate_pending(self, requests: list[dict]) -> None:
        lv = self.query_one("#pending_list", ListView)
        lv.clear()
        for r in requests:
            lv.append(FriendRequestItem(r["id"], r["sender_name"]))
        summary = (
            self.render_pending_summary(requests) if requests else "No incoming requests right now"
        )
        empty_state = "" if requests else self.render_empty_state()
        try:
            self.query_one("#pending_summary", Static).update(summary)
            self.query_one("#pending_empty", Static).update(empty_state)
        except NoMatches:
            return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "btn_add":
            username = self.query_one("#add_input", Input).value.strip()
            if username:
                self.query_one("#add_input", Input).value = ""
                self.post_message(self.SendRequest(username))
        elif btn_id == "btn_back":
            self.app.pop_screen()
        elif btn_id == "btn_exit":
            self.app.exit()
        elif btn_id.startswith("accept_"):
            self.post_message(self.AcceptRequest(btn_id.removeprefix("accept_")))

        elif btn_id.startswith("decline_"):
            self.post_message(self.DeclineRequest(btn_id.removeprefix("decline_")))

    def show_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)
        self.query_one("#error", Static).update("")

    def show_error(self, msg: str) -> None:
        self.query_one("#error", Static).update(msg)
        self.query_one("#status", Static).update("")
