"""Idea Brief screen for displaying and editing the generated brief."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Markdown, Static, TextArea

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.mixins import MentionProcessingMixin
from waypoints.tui.screens.transition_guard import can_enter_state
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

logger = logging.getLogger(__name__)


class IdeaBriefScreen(Screen[None]):
    """
    Idea Brief screen - Display generated brief with editing capability.

    User can edit the brief, then proceed to Product Specification.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed_to_spec", "Continue", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+e", "toggle_edit", "Edit", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = """
    IdeaBriefScreen {
        background: $surface;
        overflow: hidden;
    }

    IdeaBriefScreen .content {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }

    IdeaBriefScreen .generating {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    IdeaBriefScreen .generating Static {
        color: $text-muted;
    }

    IdeaBriefScreen Markdown {
        width: 100%;
        padding: 0 2;
        background: transparent;
    }

    IdeaBriefScreen TextArea {
        width: 100%;
        height: 1fr;
        border: none;
        background: transparent;
        display: none;
    }

    IdeaBriefScreen TextArea.editing {
        display: block;
    }

    IdeaBriefScreen VerticalScroll.editing {
        display: none;
    }

    IdeaBriefScreen .file-path {
        dock: bottom;
        color: $text-muted;
        padding: 0 2;
    }

    IdeaBriefScreen ModelStatusIndicator {
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
        idea: str,
        history: DialogueHistory,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.idea = idea
        self.history = history
        self.brief_content: str = ""
        self.is_editing: bool = False
        self._is_generating: bool = False
        self._stream_file_path: Path | None = None
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
        """Get the path to the latest brief file."""
        docs_dir = self.project.get_docs_path()
        pattern = "idea-brief-*.md"
        matching_files = sorted(docs_dir.glob(pattern), reverse=True)

        if matching_files:
            return matching_files[0]
        else:
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            return docs_dir / f"idea-brief-{timestamp}.md"

    def compose(self) -> ComposeResult:
        yield Header()
        yield ModelStatusIndicator(id="model-status")
        with VerticalScroll(classes="content", id="brief-content"):
            with VerticalScroll(classes="generating", id="generating-view"):
                yield ThinkingIndicator()
                yield Static("Generating idea brief...")
            yield Markdown("", id="brief-display")
        yield TextArea(id="brief-editor")
        yield Static("", classes="file-path", id="file-path")
        yield Footer()

    def on_mount(self) -> None:
        """Start generating the brief."""
        self.app.sub_title = f"{self.project.name} · Idea Brief"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Transition journey state: SHAPE_QA -> SHAPE_BRIEF_GENERATING
        self.coordinator.transition(
            JourneyState.SHAPE_BRIEF_GENERATING,
            reason="idea_brief.generate",
        )

        self._is_generating = True
        self._generate_brief()

    @work(thread=True)
    def _generate_brief(self) -> None:
        """Generate idea brief via coordinator."""
        logger.info(
            "Generating idea brief from %d messages", len(self.history.messages)
        )

        # Start thinking indicator
        self.app.call_from_thread(self._set_thinking, True)

        accumulated_content = ""

        def on_chunk(chunk: str) -> None:
            nonlocal accumulated_content
            accumulated_content += chunk
            self.brief_content = accumulated_content
            self._persist_stream_snapshot()
            self.app.call_from_thread(self._update_brief_display, accumulated_content)

        try:
            # Coordinator generates brief, saves to disk, and generates summary
            brief_content = self.coordinator.generate_idea_brief(
                history=self.history,
                on_chunk=on_chunk,
            )
            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            self.brief_content = brief_content
            self._persist_stream_snapshot()
            self.app.call_from_thread(self._finalize_brief)

        except Exception as e:
            self._is_generating = False
            logger.exception("Error generating brief: %s", e)
            self.brief_content = f"# Error\n\nFailed to generate brief: {e}"
            self.app.call_from_thread(self._update_brief_display, self.brief_content)
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

        # Stop thinking indicator.
        self.app.call_from_thread(self._set_thinking, False)

    def _next_output_file_path(self) -> Path:
        """Allocate a stable output file for this generation run."""
        if self._stream_file_path is None:
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            docs_dir = self.project.get_docs_path()
            docs_dir.mkdir(parents=True, exist_ok=True)
            self._stream_file_path = docs_dir / f"idea-brief-{timestamp}.md"
        return self._stream_file_path

    def _persist_stream_snapshot(self) -> None:
        """Persist latest generated content to disk during streaming."""
        try:
            self._next_output_file_path().write_text(self.brief_content)
        except OSError as e:
            logger.exception("Failed to persist streamed brief: %s", e)

    def _set_thinking(self, thinking: bool) -> None:
        """Toggle the model status indicator."""
        self.query_one("#model-status", ModelStatusIndicator).set_thinking(thinking)

    def _update_brief_display(self, content: str) -> None:
        """Update the brief display during streaming."""
        self.query_one("#generating-view").display = False
        display = self.query_one("#brief-display", Markdown)
        display.update(content)

    def _finalize_brief(self) -> None:
        """Finalize brief display after generation."""
        editor = self.query_one("#brief-editor", TextArea)
        editor.text = self.brief_content

        # Update file path display (coordinator already saved)
        file_path = self._stream_file_path or self._get_latest_file_path()
        self.query_one("#file-path", Static).update(str(file_path))

        # Transition journey state: SHAPE_BRIEF_GENERATING -> SHAPE_BRIEF_REVIEW
        self.coordinator.transition(
            JourneyState.SHAPE_BRIEF_REVIEW,
            reason="idea_brief.review",
        )

        self.notify(f"Saved to {file_path.name}", severity="information")
        logger.info("Brief generation complete: %d chars", len(self.brief_content))
        self._is_generating = False

    def _save_to_disk(self) -> None:
        """Save the brief content to disk (for edits)."""
        file_path = self._stream_file_path or self._get_latest_file_path()
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(self.brief_content)
            logger.info("Saved brief to %s", file_path)
            self.notify(f"Saved to {file_path.name}", severity="information")
        except OSError as e:
            logger.exception("Failed to save brief: %s", e)
            self.notify(f"Failed to save: {e}", severity="error")

    def action_toggle_edit(self) -> None:
        """Toggle between view and edit modes."""
        self.is_editing = not self.is_editing

        content_scroll = self.query_one("#brief-content", VerticalScroll)
        editor = self.query_one("#brief-editor", TextArea)

        if self.is_editing:
            content_scroll.add_class("editing")
            editor.add_class("editing")
            editor.text = self.brief_content
            editor.focus()
            self.app.sub_title = f"{self.project.name} · Idea Brief (editing)"
        else:
            # Save edits
            self.brief_content = editor.text
            display = self.query_one("#brief-display", Markdown)
            display.update(self.brief_content)
            content_scroll.remove_class("editing")
            editor.remove_class("editing")
            self._save_to_disk()
            self.app.sub_title = f"{self.project.name} · Idea Brief"

    def action_save(self) -> None:
        """Save the current content to disk."""
        if self.is_editing:
            editor = self.query_one("#brief-editor", TextArea)
            self.brief_content = editor.text
        self._save_to_disk()

    def action_proceed_to_spec(self) -> None:
        """Proceed to Product Specification phase."""
        if self._is_generating:
            self.notify("Idea brief generation is still running. Please wait.")
            return

        if self.is_editing:
            editor = self.query_one("#brief-editor", TextArea)
            self.brief_content = editor.text

        if not self.brief_content.strip():
            self.notify("Idea brief is empty. Wait for generation to complete.")
            return

        journey = self.project.journey
        if not can_enter_state(journey, JourneyState.SHAPE_SPEC_GENERATING):
            state_label = journey.state.value if journey else "unknown"
            logger.warning(
                "Cannot continue to product spec from state %s; redirecting to resume",
                state_label,
            )
            self.notify("Current state changed. Redirecting to the correct screen.")
            self.app.call_later(self._redirect_to_current_phase)
            return

        # Save before proceeding
        self._save_to_disk()

        self.app.switch_phase(  # type: ignore
            "product-spec",
            {
                "project": self.project,
                "idea": self.idea,
                "brief": self.brief_content,
                "history": self.history,
            },
        )

    def _redirect_to_current_phase(self) -> None:
        """Route to screen matching current persisted journey phase."""
        self.waypoints_app._resume_project(self.project)

    def action_back(self) -> None:
        """Go back to project selection."""
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.app.switch_screen(ProjectSelectionScreen())


class IdeaBriefResumeScreen(Screen[None], MentionProcessingMixin):
    """
    Resume screen for Idea Brief - displays existing brief without regenerating.

    Used when resuming a project that already has a generated brief.
    Supports @waypoints mentions for AI-assisted editing (Ctrl+R).
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed_to_spec", "Continue", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+f", "forward", "Forward", show=True),
        Binding("e", "edit_external", "Edit", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
        *MentionProcessingMixin.MENTION_BINDINGS,
    ]

    DEFAULT_CSS = """
    IdeaBriefResumeScreen {
        background: $surface;
        overflow: hidden;
    }

    IdeaBriefResumeScreen .content {
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }

    IdeaBriefResumeScreen Markdown {
        width: 100%;
        padding: 0 2;
        background: transparent;
    }

    IdeaBriefResumeScreen TextArea {
        width: 100%;
        height: 1fr;
        border: none;
        background: transparent;
        display: none;
    }

    IdeaBriefResumeScreen TextArea.editing {
        display: block;
    }

    IdeaBriefResumeScreen VerticalScroll.editing {
        display: none;
    }

    IdeaBriefResumeScreen .file-path {
        dock: bottom;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def __init__(
        self,
        project: Project,
        brief: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.brief_content = brief
        self.is_editing: bool = False
        self._init_mention_state()

    # --- MentionProcessingMixin protocol implementation ---

    @property
    def document_content(self) -> str:
        """Get current document content for mention processing."""
        return self.brief_content

    @document_content.setter
    def document_content(self, value: str) -> None:
        """Set document content from mention processing."""
        self.brief_content = value

    @property
    def document_type(self) -> str:
        """Document type identifier for mention processing."""
        return "idea-brief"

    def _get_docs_path(self) -> Path:
        """Get docs directory path for mention processing."""
        return self.project.get_docs_path()

    def _update_mention_display(self, content: str) -> None:
        """Update display after mention processing."""
        display = self.query_one("#brief-display", Markdown)
        display.update(content)

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(classes="content", id="brief-content"):
            yield Markdown(self.brief_content, id="brief-display")
        yield TextArea(id="brief-editor")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the resumed brief view."""
        self.app.sub_title = f"{self.project.name} · Idea Brief"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Pre-populate editor with existing content
        editor = self.query_one("#brief-editor", TextArea)
        editor.text = self.brief_content

        logger.info("Resumed idea brief for project: %s", self.project.slug)

    def _get_brief_file_path(self) -> Path:
        """Get the path to the latest brief file."""
        docs_dir = self.project.get_docs_path()
        pattern = "idea-brief-*.md"
        matching_files = sorted(docs_dir.glob(pattern), reverse=True)

        if matching_files:
            return matching_files[0]
        else:
            timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            return docs_dir / f"idea-brief-{timestamp}.md"

    def action_edit_external(self) -> None:
        """Open brief in external editor."""
        from waypoints.tui.utils import edit_file_in_editor

        # Save current content first
        file_path = self._get_brief_file_path()
        file_path.write_text(self.brief_content)

        if not edit_file_in_editor(self.app, file_path, self._reload_content):
            self.notify(
                "Editor not allowed. Set $EDITOR to vim, code, etc.",
                severity="error",
            )

    def _reload_content(self) -> None:
        """Reload content after external edit."""
        file_path = self._get_brief_file_path()
        self.brief_content = file_path.read_text()

        # Update display
        display = self.query_one("#brief-display", Markdown)
        display.update(self.brief_content)

        # Update editor too in case user switches to inline edit
        editor = self.query_one("#brief-editor", TextArea)
        editor.text = self.brief_content

        self.notify("Brief reloaded")

    def action_save(self) -> None:
        """Save the current content to disk."""
        self._save_to_disk()

    def _save_to_disk(self) -> None:
        """Save the brief content to disk (overwriting latest)."""
        file_path = self._get_brief_file_path()
        try:
            file_path.write_text(self.brief_content)
            logger.info("Saved brief to %s", file_path)
            self.notify(f"Saved to {file_path.name}", severity="information")
        except OSError as e:
            logger.exception("Failed to save brief: %s", e)
            self.notify(f"Failed to save: {e}", severity="error")

    def action_proceed_to_spec(self) -> None:
        """Proceed to Product Specification phase."""
        if self.is_editing:
            editor = self.query_one("#brief-editor", TextArea)
            self.brief_content = editor.text

        # Save before proceeding
        self._save_to_disk()

        self.app.switch_phase(  # type: ignore
            "product-spec",
            {
                "project": self.project,
                "idea": self.project.initial_idea,
                "brief": self.brief_content,
            },
        )

    def action_back(self) -> None:
        """Go back to project selection."""
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.app.switch_screen(ProjectSelectionScreen())

    def action_forward(self) -> None:
        """Go forward to Product Spec screen (if spec exists)."""
        spec = self.app._load_latest_doc(self.project, "product-spec")  # type: ignore[attr-defined]
        if spec:
            from waypoints.tui.screens.product_spec import ProductSpecResumeScreen

            self.app.switch_screen(
                ProductSpecResumeScreen(
                    project=self.project, spec=spec, brief=self.brief_content
                )
            )
        else:
            self.notify("No product spec yet. Press Ctrl+Enter to generate one.")
