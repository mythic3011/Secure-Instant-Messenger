from __future__ import annotations

from textual.widgets import Static

from client.ui.contracts import UIBanner
from client.ui.theme import Theme


class Banner(Static):
    DEFAULT_CSS = f"""
    Banner {{
        color: #10141f;
        height: auto;
        background: {Theme.SURFACE_PANEL};
        padding: 1 2;
        border-left: thick #4b5563;
        display: none;
    }}
    Banner.-visible {{
        display: block;
    }}
    Banner.-warning {{
        background: {Theme.WARNING_BG};
        border-left: thick {Theme.WARNING};
        color: {Theme.WARNING_SOFT};
    }}
    Banner.-error {{
        background: {Theme.ERROR_BG};
        border-left: thick {Theme.ERROR};
        color: {Theme.ERROR_SOFT};
    }}
    Banner.-info {{
        background: {Theme.INFO_BG};
        border-left: thick {Theme.INFO};
        color: {Theme.INFO_SOFT};
    }}
    """

    def __init__(self, banner: UIBanner | None = None, *, id: str | None = None) -> None:
        super().__init__("", id=id)
        self.current_banner: UIBanner | None = None
        self.current_severity = "info"
        self.current_text = ""
        self.show_banner(banner)

    def show_banner(self, banner: UIBanner | None) -> None:
        self.current_banner = banner
        self.remove_class("-visible", "-warning", "-error", "-info")
        if banner is None:
            self.current_severity = "info"
            self.current_text = ""
            self.update("")
            return

        self.current_severity = banner.severity
        self.current_text = f"{banner.title}\n{banner.message}"
        self.add_class("-visible", f"-{banner.severity}")
        self.update(self.current_text)
