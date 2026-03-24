"""
client/ui/screens/login.py — Login and registration screens.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static


class LoginScreen(Screen):
    """
    Login screen: username + password + TOTP code.
    On success, posts LoginSuccess message to the app.
    """

    class Exit(Message):
        pass

    CSS = """
    LoginScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #btn_exit {
        background: #0d0d1a;
        color: #ff4444;
        border: tall #ff4444;
    }
    #btn_exit:hover { background: #1a0000; }
    #panel {
        width: 56;
        height: auto;
        border: double #00ff9f;
        padding: 1 3;
        background: #0d0d1a;
    }
    #title {
        color: #00ff9f;
        text-style: bold;
        content-align: center middle;
        padding: 0 0 1 0;
    }
    Input {
        margin-bottom: 1;
        border: tall #1a1a3e;
        background: #0a0a1a;
        color: #e0e0ff;
    }
    Input:focus {
        border: tall #00ff9f;
    }
    Button {
        margin-top: 1;
        width: 100%;
    }
    #btn_login {
        background: #00ff9f;
        color: #000000;
        text-style: bold;
    }
    #btn_login:hover {
        background: #00cc7f;
    }
    #btn_register {
        background: #0d0d1a;
        color: #00ff9f;
        border: tall #00ff9f;
    }
    #error { color: #ff4444; margin-bottom: 1; text-style: bold; }
    """

    class LoginSuccess(Message):
        def __init__(self, username: str, password: str, totp_code: str) -> None:
            super().__init__()
            self.username  = username
            self.password  = password
            self.totp_code = totp_code

    def __init__(self, prefill_username: str = "") -> None:
        super().__init__()
        self._prefill_username = prefill_username

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="panel"):
                yield Static("◈ COMP3334 SECURE IM ◈", id="title")
                yield Label("")
                yield Input(placeholder="Username", id="username", value=self._prefill_username)
                yield Input(placeholder="Password", password=True, id="password")
                yield Input(
                    placeholder="OTP code (6 digits)",
                    id="totp",
                    restrict=r"\d*",
                    max_length=6,
                )
                yield Static("", id="error")
                yield Button("Login", variant="primary", id="btn_login")
                yield Button("Register instead", variant="default", id="btn_register")
                yield Button("✕ Exit", variant="default", id="btn_exit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_login":
            self._do_login()
        elif event.button.id == "btn_register":
            from client.ui.screens.register import RegisterScreen
            self.app.push_screen(RegisterScreen())
        elif event.button.id == "btn_exit":
            self.post_message(self.Exit())

    def on_key(self, event) -> None:
        if event.key == "ctrl+a":
            inp = self.focused
            if isinstance(inp, Input):
                inp.action_select_all()
                event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_login()

    def _do_login(self) -> None:
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        totp     = self.query_one("#totp", Input).value.strip()
        error    = self.query_one("#error", Static)

        if not username or not password or not totp:
            error.update("All fields are required.")
            return
        if len(totp) != 6 or not totp.isdigit():
            error.update("OTP must be exactly 6 digits.")
            return

        error.update("")
        self.post_message(self.LoginSuccess(username, password, totp))
