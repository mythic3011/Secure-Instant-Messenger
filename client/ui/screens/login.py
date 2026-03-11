"""
client/ui/screens/login.py — Login and registration screens.
"""

from __future__ import annotations

from textual.message import Message
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static


class LoginScreen(Screen):
    """
    Login screen: username + password + TOTP code.
    On success, posts LoginSuccess message to the app.
    """

    CSS = """
    LoginScreen {
        align: center middle;
    }
    #panel {
        width: 50;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }
    Input { margin-bottom: 1; }
    #error { color: $error; margin-bottom: 1; }
    """

    class LoginSuccess(Message):
        def __init__(self, username: str, password: str, totp_code: str) -> None:
            super().__init__()
            self.username  = username
            self.password  = password
            self.totp_code = totp_code

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="panel"):
                yield Static("COMP3334 Secure IM", id="title")
                yield Label("")
                yield Input(placeholder="Username", id="username")
                yield Input(placeholder="Password", password=True, id="password")
                yield Input(placeholder="OTP code (6 digits)", id="totp")
                yield Static("", id="error")
                yield Button("Login", variant="primary", id="btn_login")
                yield Button("Register instead", variant="default", id="btn_register")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_login":
            self._do_login()
        elif event.button.id == "btn_register":
            from client.ui.screens.register import RegisterScreen
            self.app.push_screen(RegisterScreen())

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
