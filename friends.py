"""
client/ui/screens/friends.py — Friend request management screen.
FIXED: Added explicit event triggering to force conversation list refresh
when a request is accepted, ensuring the new friend appears immediately.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Input, ListItem, ListView, Static

# Import ConversationListScreen so we can post its RequestReload
from client.ui.screens.conversations import ConversationListScreen


class FriendRequestItem(ListItem):
    def __init__(self, request_id: str, sender_name: str) -> None:
        super().__init__()
        self.request_id = request_id
        self.sender_name = sender_name

    def compose(self) -> ComposeResult:
        with Horizontal(classes="req_row"):
            yield Static(f"  ⊛ {self.sender_name}", classes="req_name")
            yield Button("✓ Accept", variant="success", id=f"accept_{self.request_id}", classes="req_btn")
            yield Button("✕ Decline", variant="error", id=f"decline_{self.request_id}", classes="req_btn")


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
        padding: 0 2;
    }

    .section_label {
        height: 2;
        background: #0d0d1a;
        border-bottom: solid #1a1a3e;
        color: #8888cc;
        text-style: italic;
        padding: 0 2;
        content-align: left middle;
    }

    #pending_list {
        height: 1fr;
        border: solid #1a1a3e;
        background: #0a0a0f;
    }
    #pending_list > ListItem {
        color: #c0c0e0;
        padding: 0 0;
        height: 3;
    }
    #pending_list > ListItem:hover { background: #0d0d2a; }
    .req_row  { height: 3; align: left middle; }
    .req_name { width: 1fr; color: #e0e0ff; content-align: left middle; padding: 0 1; }
    .req_btn  { width: 12; height: 3; }

    #status { color: #00ff9f; height: 1; padding: 0 2; content-align: left middle; }
    #error  { color: #ff4444; height: 1; padding: 0 2; content-align: left middle; text-style: bold; }

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
        width: 16;
        background: #00ccff;
        color: #000000;
        text-style: bold;
    }
    #btn_add:hover { background: #00aadd; }

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
    #btn_exit {
        color: #ff4444;
        border: tall #ff4444;
    }
    #btn_exit:hover { background: #1a0000; color: #ff6666; }
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

    class _LoadPending(Message):
        pass

    def __init__(self) -> None:
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("◈ SECURE IM  ·  Friends", id="header")
        yield Static("Pending friend requests", classes="section_label")
        yield ListView(id="pending_list")
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

    def populate_pending(self, requests: list[dict]) -> None:
        lv = self.query_one("#pending_list", ListView)
        lv.clear()
        if not requests:
            self.show_status("No pending requests.")
        for r in requests:
            lv.append(FriendRequestItem(r["id"], r["sender_name"]))

    def handle_action_result(self, success: bool, message: str, request_id: str | None = None) -> None:
        """
        Refreshes local pending list and triggers global conversation refresh.
        Must be called by the App after backend processes Accept/Decline.
        """
        if success:
            self.show_status(message)
            # Refresh this screen's pending list
            self.app.call_later(self._load_pending)
            # Trigger the conversation list to reload immediately
            self.post_message(ConversationListScreen.RequestReload())
        else:
            self.show_error(message)
            if request_id:
                self.reenable_request_buttons(request_id)
            else:
                # Fallback: refresh pending list to reset UI state
                self.app.call_later(self._load_pending)

    def _set_request_buttons_disabled(self, request_id: str, disabled: bool) -> None:
        """Disable or enable both Accept/Decline buttons for a given request."""
        lv = self.query_one("#pending_list", ListView)
        for item in lv.children:
            if isinstance(item, FriendRequestItem) and item.request_id == request_id:
                for btn in item.query(Button):
                    btn.disabled = disabled

    def reenable_request_buttons(self, request_id: str) -> None:
        """Re-enable Accept/Decline buttons for a given request if action failed."""
        self._set_request_buttons_disabled(request_id, False)

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
            request_id = btn_id.removeprefix("accept_")
            # disable both buttons for this request to avoid races
            self._set_request_buttons_disabled(request_id, True)
            self.post_message(self.AcceptRequest(request_id))

        elif btn_id.startswith("decline_"):
            request_id = btn_id.removeprefix("decline_")
            self._set_request_buttons_disabled(request_id, True)
            self.post_message(self.DeclineRequest(request_id))

    def show_status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)
        self.query_one("#error", Static).update("")

    def show_error(self, msg: str) -> None:
        self.query_one("#error", Static).update(msg)
        self.query_one("#status", Static).update("")
