from __future__ import annotations

from typing import Literal

from textual.widgets import Static

from client.ui.theme import Theme

BadgeTone = Literal["neutral", "info", "warning", "error", "success"]


class SecurityBadge(Static):
    DEFAULT_CSS = f"""
    SecurityBadge {{
        color: {Theme.INFO_SOFT};
        background: {Theme.INFO_BG};
        border-left: thick {Theme.INFO};
        padding: 0 1;
        text-style: bold;
        margin: 1 0 0 0;
    }}
    SecurityBadge.-neutral {{
        color: {Theme.TEXT_PRIMARY};
        background: {Theme.SURFACE};
        border-left: thick #4b5563;
    }}
    SecurityBadge.-info {{
        color: {Theme.INFO_SOFT};
        background: {Theme.INFO_BG};
        border-left: thick {Theme.INFO};
    }}
    SecurityBadge.-warning {{
        color: {Theme.WARNING_SOFT};
        background: {Theme.WARNING_BG};
        border-left: thick {Theme.WARNING};
    }}
    SecurityBadge.-error {{
        color: {Theme.ERROR_SOFT};
        background: {Theme.ERROR_BG};
        border-left: thick {Theme.ERROR};
    }}
    SecurityBadge.-success {{
        color: {Theme.SUCCESS_SOFT};
        background: {Theme.SUCCESS_BG};
        border-left: thick {Theme.SUCCESS};
    }}
    """

    def __init__(self, label: str = "", *, tone: BadgeTone = "neutral", id: str | None = None) -> None:
        super().__init__(label, id=id)
        self.label = label
        self.tone: BadgeTone = "neutral"
        self.set_badge(label, tone=tone)

    def set_badge(self, label: str, *, tone: BadgeTone = "neutral") -> None:
        self.label = label
        self.tone = tone
        self.remove_class("-neutral", "-info", "-warning", "-error", "-success")
        self.add_class(f"-{tone}")
        self.update(label)
