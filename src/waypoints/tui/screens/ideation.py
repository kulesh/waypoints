"""Ideation screen for initial idea entry."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static, TextArea

from waypoints.models import JourneyState, Project


class IdeationScreen(Screen):
    """
    Ideation screen - Initial idea entry.

    User enters their idea in a text area and begins the journey to Product Spec.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+enter", "begin_journey", "Continue"),
        Binding("escape", "back", "Back", show=True),
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

    IdeationScreen .label {
        color: $text-muted;
        padding: 0 0 0 0;
    }

    IdeationScreen #project-name {
        width: 100%;
        margin: 0 0 1 0;
        border: none;
        background: transparent;
    }

    IdeationScreen #project-name:focus {
        border: none;
    }

    IdeationScreen .prompt {
        color: $text-muted;
        padding: 0 0 0 0;
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
            yield Static("Project name:", classes="label")
            yield Input(placeholder="e.g., AI Task Manager", id="project-name")
            yield Static("What would you like to build?", classes="prompt")
            yield TextArea(id="idea-input")
            yield Static(
                "Paste text, drop a file, or type your idea. "
                "Press Ctrl+Enter to continue.",
                classes="hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        """Focus the project name input on mount."""
        self.app.sub_title = "Ideation"
        self.query_one("#project-name", Input).focus()

    def action_begin_journey(self) -> None:
        """Transition to Ideation Q&A phase with the idea."""
        project_name = self.query_one("#project-name", Input).value.strip()
        idea_text = self.query_one("#idea-input", TextArea).text.strip()

        if not project_name:
            self.notify("Please enter a project name", severity="warning")
            self.query_one("#project-name", Input).focus()
            return

        if not idea_text:
            self.notify("Please enter an idea", severity="warning")
            self.query_one("#idea-input", TextArea).focus()
            return

        # Create and save the project
        project = Project.create(name=project_name, idea=idea_text)
        self.notify(f"Created project: {project.slug}", severity="information")

        # Transition journey state: SPARK_IDLE -> SPARK_ENTERING
        project.transition_journey(JourneyState.SPARK_ENTERING)

        self.app.switch_phase(  # type: ignore
            "ideation-qa",
            {"project": project, "idea": idea_text},
        )

    def action_back(self) -> None:
        """Go back to project selection."""
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.app.switch_screen(ProjectSelectionScreen())
