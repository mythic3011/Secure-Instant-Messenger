"""
client/ui/screens/settings.py — Fingerprint display and TTL configuration.
Covers: R5 (fingerprint / safety number), R10 (TTL setting)
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Button, Input, Static


class SettingsScreen(Screen):
    """
    Per-conversation settings:
      - Display safety number (fingerprint) for key verification (R5)
      - Set default TTL for self-destruct messages (R10)
    """

    CSS = """
    SettingsScreen {
        align: center middle;
        background: #0a0a0f;
    }
    #panel {
        width: 64;
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
    #fingerprint {
        color: #00ff9f;
        text-style: bold;
        margin: 1 0;
        background: #001a0d;
        padding: 0 1;
        border: solid #00ff9f;
    }
    #ttl_input {
        margin-bottom: 1;
        border: tall #1a1a3e;
        background: #0a0a1a;
        color: #e0e0ff;
    }
    #ttl_input:focus { border: tall #00ccff; }
    Button { margin-top: 1; width: 100%; }
    #btn_save_ttl {
        background: #00ccff;
        color: #000000;
        text-style: bold;
    }
    #btn_verify {
        background: #00ff9f;
        color: #000000;
        text-style: bold;
    }
    #btn_close {
        background: #0d0d1a;
        color: #00ccff;
        border: tall #1a1a3e;
    }
    #status { color: #00ff9f; text-style: bold; }
    """

    class SetTTL(Message):
        def __init__(self, ttl_seconds: int | None) -> None:
            super().__init__()
            self.ttl_seconds = ttl_seconds

    class MarkVerified(Message):
        pass

    def __init__(self, conversation_id: str, peer_username: str) -> None:
        super().__init__()
        self.conversation_id = conversation_id
        self.peer_username   = peer_username
        self._fingerprint    = "Loading…"

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static(f"⚙ Settings — {self.peer_username}", id="title")
            yield Static("")
            yield Static("Safety Number (verify out-of-band with your contact):", id="fp_label")
            yield Static(self._fingerprint, id="fingerprint")
            yield Button("✓ Mark as Verified", variant="success", id="btn_verify")
            yield Static("")
            yield Static("Self-destruct TTL (seconds, blank = no expiry):", id="ttl_label")
            yield Input(placeholder="e.g. 300 for 5 minutes", id="ttl_input", restrict=r"\d*")
            yield Static("", id="status")
            yield Button("💾 Save TTL", variant="primary", id="btn_ttl")
            yield Button("← Close", id="btn_close")

    def on_mount(self) -> None:
        self.app.call_later(self._load_fingerprint)

    async def _load_fingerprint(self) -> None:
        # App layer will call set_fingerprint() after computing it
        self.post_message(self._RequestFingerprint(self.conversation_id))

    class _RequestFingerprint(Message):
        def __init__(self, conversation_id: str) -> None:
            super().__init__()
            self.conversation_id = conversation_id

    def set_fingerprint(self, fp: str) -> None:
        self.query_one("#fingerprint", Static).update(fp)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("btn_back", "btn_close"):
            self.app.pop_screen()
        elif event.button.id == "btn_verify":
            self.post_message(self.MarkVerified())
            self.query_one("#status", Static).update("Marked as verified.")
        elif event.button.id == "btn_ttl":
            self._save_ttl()

    def on_key(self, event) -> None:
        if event.key == "ctrl+a":
            inp = self.focused
            if isinstance(inp, Input):
                inp.action_select_all()
                event.stop()

    def _save_ttl(self) -> None:
        raw = self.query_one("#ttl_input", Input).value.strip()
        status = self.query_one("#status", Static)
        if raw == "":
            self.post_message(self.SetTTL(None))
            status.update("TTL cleared (messages won't expire).")
            return
        try:
            ttl = int(raw)
            if not (1 <= ttl <= 604800):
                raise ValueError
        except ValueError:
            status.update("TTL must be 1–604800 seconds.")
            return
        self.post_message(self.SetTTL(ttl))
        status.update(f"TTL set to {ttl}s.")
