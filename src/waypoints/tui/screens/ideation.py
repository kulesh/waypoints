"""Ideation screen for initial idea entry."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static, TextArea


class IdeationScreen(Screen):
    """
    Ideation screen - Initial idea entry.

    User enters their idea in a text area and begins the journey to Product Spec.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+enter", "begin_journey", "Continue"),
    ]

    DEFAULT_CSS = """
    IdeationScreen {
        background: $surface;
    }

    IdeationScreen .content {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }

    IdeationScreen .prompt {
        color: $text-muted;
        padding: 0 0 1 0;
    }

    IdeationScreen TextArea {
        width: 100%;
        height: 1fr;
        border: none;
        background: transparent;
        padding: 0;
    }

    IdeationScreen TextArea:focus {
        border: none;
    }

    IdeationScreen .hint {
        dock: bottom;
        color: $text-disabled;
        text-style: italic;
        padding: 1 0 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="content"):
            yield Static("What would you like to build?", classes="prompt")
            yield TextArea(id="idea-input")
            yield Static(
                "Paste text, drop a file, or type your idea. "
                "Press Ctrl+Enter to continue.",
                classes="hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        """Focus the text area on mount."""
        self.app.sub_title = "Ideation"
        self.query_one(TextArea).focus()

    def action_begin_journey(self) -> None:
        """Transition to Ideation Q&A phase with the idea."""
        idea_text = self.query_one(TextArea).text.strip()
        if not idea_text:
            self.notify("Please enter an idea first", severity="warning")
            return
        self.app.switch_phase("ideation-qa", {"idea": idea_text})  # type: ignore
