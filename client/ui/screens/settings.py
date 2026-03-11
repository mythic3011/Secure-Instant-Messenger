"""
client/ui/screens/settings.py — Fingerprint display and TTL configuration.
Covers: R5 (fingerprint / safety number), R10 (TTL setting)
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static


class SettingsScreen(Screen):
    """
    Per-conversation settings:
      - Display safety number (fingerprint) for key verification (R5)
      - Set default TTL for self-destruct messages (R10)
    """

    CSS = """
    SettingsScreen { align: center middle; }
    #panel {
        width: 60;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }
    #fingerprint {
        color: $success;
        text-style: bold;
        margin: 1 0;
    }
    #ttl_input { margin-bottom: 1; }
    #status { color: $success; }
    """

    class SetTTL:
        def __init__(self, ttl_seconds: int | None) -> None:
            self.ttl_seconds = ttl_seconds

    class MarkVerified:
        pass

    def __init__(self, conversation_id: str, peer_username: str) -> None:
        super().__init__()
        self.conversation_id = conversation_id
        self.peer_username   = peer_username
        self._fingerprint    = "Loading…"

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static(f"Settings — {self.peer_username}", id="title")
            yield Label("")
            yield Static("Safety Number (verify out-of-band with your contact):", id="fp_label")
            yield Static(self._fingerprint, id="fingerprint")
            yield Button("Mark as Verified ✓", variant="success", id="btn_verify")
            yield Label("")
            yield Static("Self-destruct TTL (seconds, blank = no expiry):", id="ttl_label")
            yield Input(placeholder="e.g. 300 for 5 minutes", id="ttl_input")
            yield Static("", id="status")
            yield Button("Save TTL", variant="primary", id="btn_ttl")
            yield Button("← Back", id="btn_back")

    def on_mount(self) -> None:
        self.title = "Settings"
        self.app.call_later(self._load_fingerprint)

    async def _load_fingerprint(self) -> None:
        # App layer will call set_fingerprint() after computing it
        self.post_message(self._RequestFingerprint(self.conversation_id))

    class _RequestFingerprint:
        def __init__(self, conversation_id: str) -> None:
            self.conversation_id = conversation_id

    def set_fingerprint(self, fp: str) -> None:
        self.query_one("#fingerprint", Static).update(fp)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_verify":
            self.post_message(self.MarkVerified())
            self.query_one("#status", Static).update("Marked as verified.")
        elif event.button.id == "btn_ttl":
            self._save_ttl()

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
