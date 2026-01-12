"""Idea Brief screen for displaying and editing the generated brief."""

import logging
from datetime import datetime
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

from waypoints.llm.client import ChatClient, StreamChunk, StreamComplete
from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import DialogueHistory, MessageRole
from waypoints.tui.mixins import MentionProcessingMixin
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

logger = logging.getLogger(__name__)

BRIEF_GENERATION_PROMPT = """\
Based on the ideation conversation below, generate a concise Idea Brief document.

The brief should be in Markdown format and include:

# Idea Brief: [Catchy Title]

## Problem Statement
What problem are we solving and why does it matter?

## Target Users
Who are the primary users and what are their pain points?

## Proposed Solution
High-level description of what we're building.

## Key Features
- Bullet points of core capabilities

## Success Criteria
How will we know if this succeeds?

## Open Questions
Any unresolved items that need further exploration.

---

Keep it concise (under 500 words). Focus on clarity over completeness.
The goal is to capture the essence of the idea so others can quickly understand it.

Here is the ideation conversation:

{conversation}

Generate the Idea Brief now:"""

BRIEF_SUMMARY_PROMPT = """\
Based on this idea brief, write a concise 100-150 word summary that captures:
- What the project is
- The core problem it solves
- Key features

Write in third person, present tense. No markdown formatting, no headers,
just plain prose. This summary will be shown in a project list view.

Idea Brief:
{brief_content}

Write the summary now (100-150 words):"""


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
        background: transparent;
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
        self.llm_client: ChatClient | None = None
        self.file_path = self._generate_file_path()

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def _generate_file_path(self) -> Path:
        """Generate a unique file path for this brief."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        docs_dir = self.project.get_docs_path()
        docs_dir.mkdir(parents=True, exist_ok=True)
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
        yield Static(str(self.file_path), classes="file-path", id="file-path")
        yield Footer()

    def on_mount(self) -> None:
        """Start generating the brief."""
        self.app.sub_title = f"{self.project.name} · Idea Brief"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Create ChatClient with metrics collector
        self.llm_client = ChatClient(
            metrics_collector=self.waypoints_app.metrics_collector,
            phase="idea-brief",
        )

        # Transition journey state: SHAPE_QA -> SHAPE_BRIEF_GENERATING
        self.project.transition_journey(JourneyState.SHAPE_BRIEF_GENERATING)

        self._generate_brief()

    @work(thread=True)
    def _generate_brief(self) -> None:
        """Generate idea brief from conversation history."""
        assert self.llm_client is not None, "llm_client not initialized"

        conversation_text = self._format_conversation()
        prompt = BRIEF_GENERATION_PROMPT.format(conversation=conversation_text)

        logger.info(
            "Generating idea brief from %d messages", len(self.history.messages)
        )

        # Start thinking indicator
        self.app.call_from_thread(self._set_thinking, True)

        brief_content = ""

        try:
            system_prompt = (
                "You are a technical writer creating concise product documentation."
            )
            for result in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            ):
                if isinstance(result, StreamChunk):
                    brief_content += result.text
                    self.app.call_from_thread(self._update_brief_display, brief_content)
                elif isinstance(result, StreamComplete):
                    # Update header cost display
                    self.app.call_from_thread(self.waypoints_app.update_header_cost)
        except Exception as e:
            logger.exception("Error generating brief: %s", e)
            brief_content = f"# Error\n\nFailed to generate brief: {e}"
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

        self.brief_content = brief_content
        self.app.call_from_thread(self._finalize_brief)
        # Stop thinking indicator
        self.app.call_from_thread(self._set_thinking, False)

    def _format_conversation(self) -> str:
        """Format dialogue history for the generation prompt."""
        parts = []
        for msg in self.history.messages:
            role = "User" if msg.role == MessageRole.USER else "Assistant"
            parts.append(f"{role}: {msg.content}")
        return "\n\n".join(parts)

    def _set_thinking(self, thinking: bool) -> None:
        """Toggle the model status indicator."""
        self.query_one("#model-status", ModelStatusIndicator).set_thinking(thinking)

    def _update_brief_display(self, content: str) -> None:
        """Update the brief display during streaming."""
        self.query_one("#generating-view").display = False
        display = self.query_one("#brief-display", Markdown)
        display.update(content)

    def _finalize_brief(self) -> None:
        """Finalize brief display after generation and save to disk."""
        editor = self.query_one("#brief-editor", TextArea)
        editor.text = self.brief_content
        self._save_to_disk()

        # Generate project summary in background
        self._generate_summary()

        # Transition journey state: SHAPE_BRIEF_GENERATING -> SHAPE_BRIEF_REVIEW
        self.project.transition_journey(JourneyState.SHAPE_BRIEF_REVIEW)

        logger.info("Brief generation complete: %d chars", len(self.brief_content))

    @work(thread=True)
    def _generate_summary(self) -> None:
        """Generate and save project summary from brief."""
        if not self.brief_content or not self.llm_client:
            return

        prompt = BRIEF_SUMMARY_PROMPT.format(brief_content=self.brief_content)

        try:
            summary = ""
            system = "You are a concise technical writer. Write plain prose."
            for result in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system,
            ):
                if isinstance(result, StreamChunk):
                    summary += result.text
                elif isinstance(result, StreamComplete):
                    pass

            # Clean up the summary (remove any accidental markdown)
            summary = summary.strip()
            self.project.summary = summary
            self.project.save()
            logger.info("Generated project summary: %d chars", len(summary))
        except Exception as e:
            logger.exception("Error generating summary: %s", e)

    def _save_to_disk(self) -> None:
        """Save the brief content to disk."""
        try:
            self.file_path.write_text(self.brief_content)
            logger.info("Saved brief to %s", self.file_path)
            self.notify(f"Saved to {self.file_path.name}", severity="information")
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
        if self.is_editing:
            editor = self.query_one("#brief-editor", TextArea)
            self.brief_content = editor.text

        # Save before proceeding
        self._save_to_disk()

        self.app.switch_phase(  # type: ignore
            "product-spec",
            {
                "project": self.project,
                "idea": self.idea,
                "brief": self.brief_content,
                "history": self.history,
                "from_phase": "idea-brief",
            },
        )

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
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
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
                "from_phase": "idea-brief",
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
