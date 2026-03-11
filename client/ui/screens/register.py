"""
client/ui/screens/register.py — Registration screen.
"""

from __future__ import annotations

import io

from textual.message import Message
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static

try:
    import qrcode as _qrcode
    _HAS_QRCODE = True
except ImportError:
    _HAS_QRCODE = False


class RegisterScreen(Screen):
    """
    Registration screen: username + password (×2).
    Crypto key generation happens in the app layer after this screen.
    """

    CSS = """
    RegisterScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #panel {
        width: 60;
        height: auto;
        border: double #00ccff;
        padding: 1 3;
        background: #0d0d1a;
    }
    #title {
        color: #00ccff;
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
    Input:focus { border: tall #00ccff; }
    Button { margin-top: 1; width: 100%; }
    #btn_register {
        background: #00ccff;
        color: #000000;
        text-style: bold;
    }
    #btn_back {
        background: #0d0d1a;
        color: #00ccff;
        border: tall #00ccff;
    }
    #error { color: #ff4444; margin-bottom: 1; text-style: bold; }
    #info  { color: #00ff9f; margin-bottom: 1; }
    """

    class RegisterRequest(Message):
        def __init__(self, username: str, password: str) -> None:
            super().__init__()
            self.username = username
            self.password = password

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="panel"):
                yield Static("Create Account", id="title")
                yield Label("")
                yield Input(placeholder="Username (3–32 chars, a-z 0-9 _-)", id="username")
                yield Input(placeholder="Password (min 12 chars)", password=True, id="password")
                yield Input(placeholder="Confirm password", password=True, id="confirm")
                yield Static("", id="error")
                yield Static("", id="info")
                yield Button("Register", variant="primary", id="btn_register")
                yield Button("Back to login", variant="default", id="btn_back")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_register":
            self._do_register()
        elif event.button.id == "btn_back":
            self.app.pop_screen()

    def _do_register(self) -> None:
        import re
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        confirm  = self.query_one("#confirm", Input).value
        error    = self.query_one("#error", Static)
        if not re.match(r"^[a-zA-Z0-9_\-]{3,32}$", username):
            error.update("Username: 3–32 chars, letters/digits/_/- only.")
            return
        if len(password) < 12:
            error.update("Password must be at least 12 characters.")
            return
        if password != confirm:
            error.update("Passwords do not match.")
            return
        error.update("")
        self.post_message(self.RegisterRequest(username, password))

    def show_totp_setup(self, totp_uri: str) -> None:
        """
        Display TOTP setup info after successful registration (Task 4 / R2).
        Shows ASCII QR code if qrcode library is available, else shows URI.
        Called by app.py after server confirms registration.
        """
        info = self.query_one("#info", Static)
        if _HAS_QRCODE:
            qr = _qrcode.QRCode(border=1)
            qr.add_data(totp_uri)
            qr.make(fit=True)
            buf = io.StringIO()
            qr.print_ascii(out=buf, invert=True)
            qr_str = buf.getvalue()
            info.update(
                f"Registered! Scan this QR code in your authenticator:\n\n"
                f"{qr_str}\n"
                f"Or enter manually:\n{totp_uri}"
            )
        else:
            info.update(
                f"Registered!\nAdd this to your authenticator app:\n{totp_uri}\n\n"
                f"(Install 'qrcode' for QR display: uv add qrcode)"
            )
        self.query_one("#error", Static).update("")