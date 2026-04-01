from __future__ import annotations

from client.ui.contracts import UIBanner
from client.ui.widgets.banner import Banner
from client.ui.widgets.security_badge import SecurityBadge


def test_banner_stores_current_banner_and_formats_title_message() -> None:
    widget = Banner()
    banner = UIBanner(
        code="key_changed",
        severity="error",
        title="Identity key changed",
        message="Verify this contact before trusting new messages.",
        persistent=True,
        dismissible=False,
    )

    widget.show_banner(banner)

    assert widget.current_banner == banner
    assert widget.current_severity == "error"
    assert widget.current_text == "Identity key changed\nVerify this contact before trusting new messages."


def test_banner_clears_to_empty_state() -> None:
    widget = Banner()
    widget.show_banner(None)

    assert widget.current_banner is None
    assert widget.current_severity == "info"
    assert widget.current_text == ""


def test_security_badge_updates_text_and_tone_without_inferring_state() -> None:
    widget = SecurityBadge()

    widget.set_badge("Action required", tone="error")

    assert widget.label == "Action required"
    assert widget.tone == "error"
