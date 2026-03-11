"""
client/ui/screens/register.py — Registration screen.
"""

from __future__ import annotations

import io
from urllib.parse import urlparse, parse_qs

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


def _extract_totp_secret(totp_uri: str) -> str:
    """Return just the base32 secret from an otpauth:// URI."""
    try:
        qs = parse_qs(urlparse(totp_uri).query)
        return qs["secret"][0]
    except (KeyError, IndexError, ValueError):
        return totp_uri  # fallback: show full URI if parsing fails


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
    #btn_exit {
        background: #0d0d1a;
        color: #ff4444;
        border: tall #ff4444;
    }
    #btn_exit:hover { background: #1a0000; }
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
    #btn_verify {
        background: #00ff9f;
        color: #000000;
        text-style: bold;
    }
    #error { color: #ff4444; margin-bottom: 1; text-style: bold; }
    #info  { color: #00ff9f; margin-bottom: 1; }
    .hidden { display: none; }
    """

    class RegisterRequest(Message):
        def __init__(self, username: str, password: str) -> None:
            super().__init__()
            self.username = username
            self.password = password

    class TotpVerify(Message):
        """Emitted when the user confirms their TOTP code after registration."""
        def __init__(self, code: str) -> None:
            super().__init__()
            self.code = code

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
                yield Button("✕ Exit", variant="default", id="btn_exit")
                yield Input(
                    placeholder="Enter the 6-digit code from your authenticator",
                    id="totp_input",
                    restrict=r"\d*",
                    max_length=6,
                    classes="hidden",
                )
                yield Button("Verify & Continue", id="btn_verify", classes="hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_register":
            self._do_register()
        elif event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_exit":
            self.app.exit()
        elif event.button.id == "btn_verify":
            self._do_verify_totp()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "totp_input":
            self._do_verify_totp()

    def _do_verify_totp(self) -> None:
        code = self.query_one("#totp_input", Input).value.strip()
        error = self.query_one("#error", Static)
        if not code.isdigit() or len(code) != 6:
            error.update("Please enter the 6-digit code from your authenticator app.")
            return
        error.update("")
        self.post_message(self.TotpVerify(code))

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
        Hides the registration form and shows the QR code + OTP verify input.
        Called by app.py after the server confirms registration.
        """
        # Hide registration form widgets (and exit — account already created, must finish TOTP)
        for widget_id in ("#username", "#password", "#confirm", "#btn_register", "#btn_back", "#btn_exit"):
            self.query_one(widget_id).add_class("hidden")

        # Build and display QR / URI in the info label
        info = self.query_one("#info", Static)
        secret = _extract_totp_secret(totp_uri)
        if _HAS_QRCODE:
            qr = _qrcode.QRCode(border=1)
            qr.add_data(totp_uri)
            qr.make(fit=True)
            buf = io.StringIO()
            qr.print_ascii(out=buf, invert=True)
            qr_str = buf.getvalue()
            info.update(
                f"Scan this QR code in your authenticator app:\n\n"
                f"{qr_str}\n"
                f"Or enter manually:\n{secret}\n\n"
                f"Then enter the 6-digit code below to confirm setup."
            )
        else:
            info.update(
                f"Enter this secret key in your authenticator app:\n{secret}\n\n"
                f"Then enter the 6-digit code below to confirm setup.\n"
                f"(Install 'qrcode' for QR display: uv add qrcode)"
            )
        self.query_one("#error", Static).update("")

        # Reveal TOTP verify widgets and focus the input
        totp_input = self.query_one("#totp_input", Input)
        totp_input.remove_class("hidden")
        self.query_one("#btn_verify").remove_class("hidden")
        totp_input.focus()