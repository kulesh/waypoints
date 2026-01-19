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

from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.mixins import MentionProcessingMixin
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

logger = logging.getLogger(__name__)


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
        background: initial;
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
        self._coordinator: JourneyCoordinator | None = None

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    @property
    def coordinator(self) -> JourneyCoordinator:
        """Get the coordinator, creating if needed."""
        if self._coordinator is None:
            self._coordinator = JourneyCoordinator(
                project=self.project,
                metrics=self.waypoints_app.metrics_collector,
            )
        return self._coordinator

    def _get_latest_file_path(self) -> Path:
        """Get the path to the latest spec file."""
        docs_dir = self.project.get_docs_path()
        pattern = "product-spec-*.md"
        matching_files = sorted(docs_dir.glob(pattern), reverse=True)

        if matching_files:
            return matching_files[0]
        else:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
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
        yield Static("", classes="file-path", id="file-path")
        yield Footer()

    def on_mount(self) -> None:
        """Start generating the specification."""
        self.app.sub_title = f"{self.project.name} · Product Spec"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Transition journey state: SHAPE_BRIEF_REVIEW -> SHAPE_SPEC_GENERATING
        self.project.transition_journey(JourneyState.SHAPE_SPEC_GENERATING)

        self._generate_spec()

    @work(thread=True)
    def _generate_spec(self) -> None:
        """Generate product specification via coordinator."""
        logger.info("Generating product spec from brief: %d chars", len(self.brief))

        # Start thinking indicator
        self.app.call_from_thread(self._set_thinking, True)

        accumulated_content = ""

        def on_chunk(chunk: str) -> None:
            nonlocal accumulated_content
            accumulated_content += chunk
            self.app.call_from_thread(self._update_spec_display, accumulated_content)

        try:
            # Coordinator generates spec, saves to disk, and generates summary
            spec_content = self.coordinator.generate_product_spec(
                brief=self.brief,
                on_chunk=on_chunk,
            )
            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            self.spec_content = spec_content
            self.app.call_from_thread(self._finalize_spec)

        except Exception as e:
            logger.exception("Error generating spec: %s", e)
            self.spec_content = f"# Error\n\nFailed to generate specification: {e}"
            self.app.call_from_thread(self._update_spec_display, self.spec_content)
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

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
        """Finalize spec display after generation."""
        editor = self.query_one("#spec-editor", TextArea)
        editor.text = self.spec_content

        # Update file path display (coordinator already saved)
        file_path = self._get_latest_file_path()
        self.query_one("#file-path", Static).update(str(file_path))

        # Transition journey state: SHAPE_SPEC_GENERATING -> SHAPE_SPEC_REVIEW
        self.project.transition_journey(JourneyState.SHAPE_SPEC_REVIEW)

        self.notify(f"Saved to {file_path.name}", severity="information")
        logger.info("Spec generation complete: %d chars", len(self.spec_content))

    def _save_to_disk(self) -> None:
        """Save the spec content to disk (for edits)."""
        file_path = self._get_latest_file_path()
        try:
            file_path.write_text(self.spec_content)
            logger.info("Saved spec to %s", file_path)
            self.notify(f"Saved to {file_path.name}", severity="information")
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


class ProductSpecResumeScreen(Screen[None], MentionProcessingMixin):
    """
    Resume screen for Product Spec - displays existing spec without regenerating.

    Used when resuming a project that already has a generated specification.
    Supports @waypoints mentions for AI-assisted editing (Ctrl+R).
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed_to_waypoints", "Continue", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+f", "forward", "Forward", show=True),
        Binding("e", "edit_external", "Edit", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
        *MentionProcessingMixin.MENTION_BINDINGS,
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
        self._init_mention_state()

    # --- MentionProcessingMixin protocol implementation ---

    @property
    def document_content(self) -> str:
        """Get current document content for mention processing."""
        return self.spec_content

    @document_content.setter
    def document_content(self, value: str) -> None:
        """Set document content from mention processing."""
        self.spec_content = value

    @property
    def document_type(self) -> str:
        """Document type identifier for mention processing."""
        return "product-spec"

    def _get_docs_path(self) -> Path:
        """Get docs directory path for mention processing."""
        return self.project.get_docs_path()

    def _update_mention_display(self, content: str) -> None:
        """Update display after mention processing."""
        display = self.query_one("#spec-display", Markdown)
        display.update(content)

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

        if not edit_file_in_editor(self.app, file_path, self._reload_content):
            self.notify(
                "Editor not allowed. Set $EDITOR to vim, code, etc.",
                severity="error",
            )

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
