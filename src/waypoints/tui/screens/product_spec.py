"""Product Specification screen for generating detailed spec from idea brief."""

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Markdown, Static, TextArea

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.llm.client import ChatClient, StreamChunk, StreamComplete
from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

logger = logging.getLogger(__name__)

SPEC_GENERATION_PROMPT = """\
Based on the Idea Brief below, generate a comprehensive Product Specification.

The specification should be detailed enough for engineers and product managers
to understand exactly what needs to be built. Use Markdown format.

# Product Specification: [Product Name]

## 1. Executive Summary
Brief overview of the product and its value proposition.

## 2. Problem Statement
### 2.1 Current Pain Points
### 2.2 Impact of the Problem
### 2.3 Why Now?

## 3. Target Users
### 3.1 Primary Persona
### 3.2 Secondary Personas
### 3.3 User Journey

## 4. Product Overview
### 4.1 Vision Statement
### 4.2 Core Value Proposition
### 4.3 Key Differentiators

## 5. Features & Requirements
### 5.1 MVP Features (Must Have)
### 5.2 Phase 2 Features (Should Have)
### 5.3 Future Considerations (Nice to Have)

## 6. Technical Considerations
### 6.1 Architecture Overview
### 6.2 Technology Stack Recommendations
### 6.3 Integration Requirements
### 6.4 Security & Privacy

## 7. Success Metrics
### 7.1 Key Performance Indicators
### 7.2 Success Criteria for MVP

## 8. Risks & Mitigations
### 8.1 Technical Risks
### 8.2 Market Risks
### 8.3 Mitigation Strategies

## 9. FAQ
Common questions and answers for the development team.

## 10. Appendix
### 10.1 Glossary
### 10.2 References

---

Here is the Idea Brief to expand:

{brief}

Generate the complete Product Specification now:"""


class ProductSpecScreen(Screen[None]):
    """
    Product Specification screen - Generate detailed spec from idea brief.

    Displays the generated spec with editing capability.
    User can edit and then proceed to Waypoints planning.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed_to_waypoints", "Continue", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+e", "toggle_edit", "Edit", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = """
    ProductSpecScreen {
        background: $surface;
        overflow: hidden;
    }

    ProductSpecScreen .content {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }

    ProductSpecScreen .generating {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    ProductSpecScreen .generating Static {
        color: $text-muted;
    }

    ProductSpecScreen Markdown {
        width: 100%;
        padding: 0 2;
        background: transparent;
    }

    ProductSpecScreen TextArea {
        width: 100%;
        height: 1fr;
        border: none;
        background: transparent;
        display: none;
    }

    ProductSpecScreen TextArea.editing {
        display: block;
    }

    ProductSpecScreen VerticalScroll.editing {
        display: none;
    }

    ProductSpecScreen .file-path {
        dock: bottom;
        color: $text-muted;
        padding: 0 2;
    }

    ProductSpecScreen ModelStatusIndicator {
        dock: top;
        layer: above;
        margin: 0 0 0 1;
        height: 1;
        width: 2;
        background: transparent;
    }
    """

    def __init__(
        self,
        project: Project,
        idea: str | None = None,
        brief: str | None = None,
        history: DialogueHistory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.idea = idea or ""
        self.brief = brief or ""
        self.history = history
        self.spec_content: str = ""
        self.is_editing: bool = False
        self.llm_client: ChatClient | None = None
        self.file_path = self._generate_file_path()

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def _generate_file_path(self) -> Path:
        """Generate a unique file path for this spec."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        docs_dir = self.project.get_docs_path()
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir / f"product-spec-{timestamp}.md"

    def compose(self) -> ComposeResult:
        yield Header()
        yield ModelStatusIndicator(id="model-status")
        with VerticalScroll(classes="content", id="spec-content"):
            with Vertical(classes="generating", id="generating-view"):
                yield ThinkingIndicator()
                yield Static("Generating product specification...")
            yield Markdown("", id="spec-display")
        yield TextArea(id="spec-editor")
        yield Static(str(self.file_path), classes="file-path", id="file-path")
        yield Footer()

    def on_mount(self) -> None:
        """Start generating the specification."""
        self.app.sub_title = f"{self.project.name} · Product Spec"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Create ChatClient with metrics collector
        self.llm_client = ChatClient(
            metrics_collector=self.waypoints_app.metrics_collector,
            phase="product-spec",
        )

        # Transition journey state: SHAPE_BRIEF_REVIEW -> SHAPE_SPEC_GENERATING
        self.project.transition_journey(JourneyState.SHAPE_SPEC_GENERATING)

        self._generate_spec()

    @work(thread=True)
    def _generate_spec(self) -> None:
        """Generate product specification from idea brief."""
        assert self.llm_client is not None, "llm_client not initialized"

        prompt = SPEC_GENERATION_PROMPT.format(brief=self.brief)

        logger.info("Generating product spec from brief: %d chars", len(self.brief))

        # Start thinking indicator
        self.app.call_from_thread(self._set_thinking, True)

        spec_content = ""

        system_prompt = (
            "You are a senior product manager creating detailed "
            "product specifications. Be thorough but practical."
        )
        try:
            for result in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            ):
                if isinstance(result, StreamChunk):
                    spec_content += result.text
                    self.app.call_from_thread(self._update_spec_display, spec_content)
                elif isinstance(result, StreamComplete):
                    # Update header cost display
                    self.app.call_from_thread(self.waypoints_app.update_header_cost)
        except Exception as e:
            logger.exception("Error generating spec: %s", e)
            spec_content = f"# Error\n\nFailed to generate specification: {e}"
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

        self.spec_content = spec_content
        self.app.call_from_thread(self._finalize_spec)
        # Stop thinking indicator
        self.app.call_from_thread(self._set_thinking, False)

    def _set_thinking(self, thinking: bool) -> None:
        """Toggle the model status indicator."""
        self.query_one("#model-status", ModelStatusIndicator).set_thinking(thinking)

    def _update_spec_display(self, content: str) -> None:
        """Update the spec display during streaming."""
        self.query_one("#generating-view").display = False
        display = self.query_one("#spec-display", Markdown)
        display.update(content)

    def _finalize_spec(self) -> None:
        """Finalize spec display after generation and save to disk."""
        editor = self.query_one("#spec-editor", TextArea)
        editor.text = self.spec_content
        self._save_to_disk()

        # Transition journey state: SHAPE_SPEC_GENERATING -> SHAPE_SPEC_REVIEW
        self.project.transition_journey(JourneyState.SHAPE_SPEC_REVIEW)

        logger.info("Spec generation complete: %d chars", len(self.spec_content))

    def _save_to_disk(self) -> None:
        """Save the spec content to disk."""
        try:
            self.file_path.write_text(self.spec_content)
            logger.info("Saved spec to %s", self.file_path)
            self.notify(f"Saved to {self.file_path.name}", severity="information")
        except OSError as e:
            logger.exception("Failed to save spec: %s", e)
            self.notify(f"Failed to save: {e}", severity="error")

    def action_toggle_edit(self) -> None:
        """Toggle between view and edit modes."""
        self.is_editing = not self.is_editing

        content_scroll = self.query_one("#spec-content", VerticalScroll)
        editor = self.query_one("#spec-editor", TextArea)

        if self.is_editing:
            content_scroll.add_class("editing")
            editor.add_class("editing")
            editor.text = self.spec_content
            editor.focus()
            self.app.sub_title = f"{self.project.name} · Product Spec (editing)"
        else:
            # Save edits
            self.spec_content = editor.text
            display = self.query_one("#spec-display", Markdown)
            display.update(self.spec_content)
            content_scroll.remove_class("editing")
            editor.remove_class("editing")
            self._save_to_disk()
            self.app.sub_title = f"{self.project.name} · Product Spec"

    def action_save(self) -> None:
        """Save the current content to disk."""
        if self.is_editing:
            editor = self.query_one("#spec-editor", TextArea)
            self.spec_content = editor.text
        self._save_to_disk()

    def action_proceed_to_waypoints(self) -> None:
        """Proceed to CHART phase (waypoint planning)."""
        if self.is_editing:
            editor = self.query_one("#spec-editor", TextArea)
            self.spec_content = editor.text

        # Save before proceeding
        self._save_to_disk()

        self.app.switch_phase(  # type: ignore
            "chart",
            {
                "project": self.project,
                "spec": self.spec_content,
                "idea": self.idea,
                "brief": self.brief,
                "history": self.history,
                "from_phase": "product-spec",
            },
        )

    def action_back(self) -> None:
        """Go back to Idea Brief screen."""
        from waypoints.tui.screens.idea_brief import IdeaBriefResumeScreen

        # Load brief from disk to ensure we have content
        brief = self.app._load_latest_doc(self.project, "idea-brief")  # type: ignore[attr-defined]
        self.app.switch_screen(
            IdeaBriefResumeScreen(project=self.project, brief=brief or "")
        )


class ProductSpecResumeScreen(Screen[None]):
    """
    Resume screen for Product Spec - displays existing spec without regenerating.

    Used when resuming a project that already has a generated specification.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed_to_waypoints", "Continue", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+f", "forward", "Forward", show=True),
        Binding("e", "edit_external", "Edit", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = """
    ProductSpecResumeScreen {
        background: $surface;
        overflow: hidden;
    }

    ProductSpecResumeScreen .content {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }

    ProductSpecResumeScreen Markdown {
        width: 100%;
        padding: 0 2;
        background: transparent;
    }

    ProductSpecResumeScreen TextArea {
        width: 100%;
        height: 1fr;
        border: none;
        background: transparent;
        display: none;
    }

    ProductSpecResumeScreen TextArea.editing {
        display: block;
    }

    ProductSpecResumeScreen VerticalScroll.editing {
        display: none;
    }

    ProductSpecResumeScreen .file-path {
        dock: bottom;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def __init__(
        self,
        project: Project,
        spec: str,
        brief: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.spec_content = spec
        self.brief = brief or ""
        self.is_editing: bool = False

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="content", id="spec-content"):
            yield Markdown(self.spec_content, id="spec-display")
        yield TextArea(id="spec-editor")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the resumed spec view."""
        self.app.sub_title = f"{self.project.name} · Product Spec"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Pre-populate editor with existing content
        editor = self.query_one("#spec-editor", TextArea)
        editor.text = self.spec_content

        logger.info("Resumed product spec for project: %s", self.project.slug)

    def _get_spec_file_path(self) -> Path:
        """Get the path to the latest spec file."""
        docs_dir = self.project.get_docs_path()
        pattern = "product-spec-*.md"
        matching_files = sorted(docs_dir.glob(pattern), reverse=True)

        if matching_files:
            return matching_files[0]
        else:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return docs_dir / f"product-spec-{timestamp}.md"

    def action_edit_external(self) -> None:
        """Open spec in external editor."""
        from waypoints.tui.utils import edit_file_in_editor

        # Save current content first
        file_path = self._get_spec_file_path()
        file_path.write_text(self.spec_content)

        edit_file_in_editor(self.app, file_path, self._reload_content)

    def _reload_content(self) -> None:
        """Reload content after external edit."""
        file_path = self._get_spec_file_path()
        self.spec_content = file_path.read_text()

        # Update display
        display = self.query_one("#spec-display", Markdown)
        display.update(self.spec_content)

        # Update editor too in case user switches to inline edit
        editor = self.query_one("#spec-editor", TextArea)
        editor.text = self.spec_content

        self.notify("Spec reloaded")

    def action_save(self) -> None:
        """Save the current content to disk."""
        self._save_to_disk()

    def _save_to_disk(self) -> None:
        """Save the spec content to disk (overwriting latest)."""
        file_path = self._get_spec_file_path()
        try:
            file_path.write_text(self.spec_content)
            logger.info("Saved spec to %s", file_path)
            self.notify(f"Saved to {file_path.name}", severity="information")
        except OSError as e:
            logger.exception("Failed to save spec: %s", e)
            self.notify(f"Failed to save: {e}", severity="error")

    def action_proceed_to_waypoints(self) -> None:
        """Proceed to CHART phase (waypoint planning)."""
        if self.is_editing:
            editor = self.query_one("#spec-editor", TextArea)
            self.spec_content = editor.text

        # Save before proceeding
        self._save_to_disk()

        self.app.switch_phase(  # type: ignore
            "chart",
            {
                "project": self.project,
                "spec": self.spec_content,
                "idea": self.project.initial_idea,
                "brief": self.brief,
                "from_phase": "product-spec",
            },
        )

    def action_back(self) -> None:
        """Go back to Idea Brief screen."""
        from waypoints.tui.screens.idea_brief import IdeaBriefResumeScreen

        # Load brief from disk to ensure we have content
        brief = self.app._load_latest_doc(self.project, "idea-brief")  # type: ignore[attr-defined]
        self.app.switch_screen(
            IdeaBriefResumeScreen(project=self.project, brief=brief or "")
        )

    def action_forward(self) -> None:
        """Go forward to Chart screen (if flight plan exists)."""
        from waypoints.models.flight_plan import FlightPlanReader
        from waypoints.tui.screens.chart import ChartScreen

        flight_plan = FlightPlanReader.load(self.project)
        if flight_plan:
            self.app.switch_screen(
                ChartScreen(
                    project=self.project, spec=self.spec_content, brief=self.brief
                )
            )
        else:
            self.notify("No flight plan yet. Press Ctrl+Enter to generate one.")
