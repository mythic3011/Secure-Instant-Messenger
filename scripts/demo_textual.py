from textual.app import App, ComposeResult
from textual.widgets import Button, Footer, Header, Static


class TestApp(App):
    CSS = """
    Screen { layout: vertical; }
    #test { margin: 2; }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Textual + uv + iTerm2 Test", id="test")
        yield Footer()
        yield Button("Click!", id="btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.query_one("#test", Static).update("bruhhhhhh")

if __name__ == "__main__":
    TestApp().run()
