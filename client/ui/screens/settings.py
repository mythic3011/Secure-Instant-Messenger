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

from client.ui.contracts import TrustViewModel
from client.ui.theme import Theme
from client.ui.widgets.security_badge import SecurityBadge


class SettingsScreen(Screen):
    """
    Per-conversation settings:
      - Display safety number (fingerprint) for key verification (R5)
      - Set default TTL for self-destruct messages (R10)
    """

    CSS = (
        """
    SettingsScreen {
        align: center middle;
        background: __APP_BACKGROUND__;
    }
    #panel {
        width: 64;
        height: auto;
        border: double __ACCENT_ALT__;
        padding: 1 3;
        background: __SURFACE__;
    }
    #title {
        color: __ACCENT_ALT__;
        text-style: bold;
        content-align: center middle;
        padding: 0 0 1 0;
    }
    .section_label {
        color: __TEXT_MUTED__;
        text-style: bold;
        margin: 1 0 0 0;
    }
    #fingerprint {
        color: __ACCENT__;
        text-style: bold;
        margin: 1 0;
        background: #001a0d;
        padding: 0 1;
        border: solid __ACCENT__;
    }
    #trust_hint, #action_hint {
        color: #cbd5e1;
        margin: 0 0 1 0;
    }
    #action_hint {
        background: #17121f;
        border-left: thick #f59e0b;
        padding: 0 1;
    }
    #ttl_input {
        margin-bottom: 1;
        border: tall #1a1a3e;
        background: __SURFACE_ALT__;
        color: #e0e0ff;
    }
    #ttl_input:focus { border: tall __ACCENT_ALT__; }
    Button { margin-top: 1; width: 100%; }
    #btn_save_ttl {
        background: __ACCENT_ALT__;
        color: #000000;
        text-style: bold;
    }
    #btn_verify {
        background: __ACCENT__;
        color: #000000;
        text-style: bold;
    }
    #btn_close {
        background: __SURFACE__;
        color: __ACCENT_ALT__;
        border: tall #1a1a3e;
    }
    #status { color: __ACCENT__; text-style: bold; }
    #trust_state {
        color: #e0e0ff;
        margin: 0 0 1 0;
    }
    """.replace("__APP_BACKGROUND__", Theme.APP_BACKGROUND)
        .replace("__ACCENT_ALT__", Theme.ACCENT_ALT)
        .replace("__SURFACE__", Theme.SURFACE)
        .replace("__TEXT_MUTED__", Theme.TEXT_MUTED)
        .replace("__ACCENT__", Theme.ACCENT)
        .replace("__SURFACE_ALT__", Theme.SURFACE_ALT)
    )

    class SetTTL(Message):
        def __init__(self, ttl_seconds: int | None) -> None:
            super().__init__()
            self.ttl_seconds = ttl_seconds

    class MarkVerified(Message):
        pass

    def __init__(self, conversation_id: str, peer_username: str) -> None:
        super().__init__()
        self.conversation_id = conversation_id
        self.peer_username = peer_username
        self._trust_view_model = TrustViewModel(
            fingerprint="Loading…",
            verified=False,
            key_changed=False,
            requires_action=False,
            last_verified_at=None,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Static(f"⚙ Settings — {self.peer_username}", id="title")
            yield Static("Trust status", classes="section_label")
            yield SecurityBadge(id="trust_badge")
            yield Static("", id="trust_state")
            yield Static("", id="trust_hint")
            yield Static("Fingerprint", classes="section_label")
            yield Static("Loading…", id="fingerprint")
            yield Static("Actions", classes="section_label")
            yield Static("", id="action_hint")
            yield Button("✓ Mark as Verified", variant="success", id="btn_verify")
            yield Static("TTL defaults", classes="section_label")
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

    def set_trust_view_model(self, trust_view_model: TrustViewModel) -> None:
        self._trust_view_model = trust_view_model
        self.query_one("#fingerprint", Static).update(trust_view_model.fingerprint)
        badge = (
            "Action required"
            if trust_view_model.requires_action
            else ("Verified" if trust_view_model.verified else "Not verified")
        )
        trust_parts = ["Key changed" if trust_view_model.key_changed else "Key stable"]
        if not trust_view_model.verified or trust_view_model.key_changed:
            trust_parts.insert(0, "Verified" if trust_view_model.verified else "Not verified")
        hint = (
            trust_view_model.banner.message
            if trust_view_model.banner is not None
            else ("This contact is verified. Compare fingerprints again after any key change.")
            if trust_view_model.verified
            else (
                "This contact is not verified yet. Compare fingerprints before trusting "
                "sensitive messages."
            )
        )
        action_hint = (
            "Re-verify this contact before trusting new messages."
            if trust_view_model.requires_action
            else "No action required."
        )
        badge_widget = self.query_one("#trust_badge", SecurityBadge)
        if hasattr(badge_widget, "set_badge"):
            badge_widget.set_badge(
                badge,
                tone="error"
                if trust_view_model.requires_action
                else ("success" if trust_view_model.verified else "warning"),
            )
        else:
            badge_widget.update(badge)
        self.query_one("#trust_state", Static).update(" · ".join(trust_parts))
        self.query_one("#trust_hint", Static).update(hint)
        self.query_one("#action_hint", Static).update(action_hint)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("btn_back", "btn_close"):
            self.app.pop_screen()
        elif event.button.id == "btn_verify":
            self.post_message(self.MarkVerified())
            self.query_one("#status", Static).update("Verification requested.")
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
