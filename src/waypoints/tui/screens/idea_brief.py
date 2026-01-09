"""Idea Brief screen for displaying and editing the generated brief."""

import logging
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Markdown, Static, TextArea

from waypoints.llm.client import ChatClient
from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import DialogueHistory, MessageRole
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


class IdeaBriefScreen(Screen):
    """
    Idea Brief screen - Display generated brief with editing capability.

    User can edit the brief, then proceed to Product Specification.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed_to_spec", "Continue", show=True),
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
    }
    """

    def __init__(
        self,
        project: Project,
        idea: str,
        history: DialogueHistory,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.idea = idea
        self.history = history
        self.brief_content: str = ""
        self.is_editing: bool = False
        self.llm_client = ChatClient()
        self.file_path = self._generate_file_path()

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

        # Transition journey state: SHAPE_QA -> SHAPE_BRIEF_GENERATING
        self.project.transition_journey(JourneyState.SHAPE_BRIEF_GENERATING)

        self._generate_brief()

    @work(thread=True)
    def _generate_brief(self) -> None:
        """Generate idea brief from conversation history."""
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
            for chunk in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            ):
                brief_content += chunk
                self.app.call_from_thread(self._update_brief_display, brief_content)
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

        # Transition journey state: SHAPE_BRIEF_GENERATING -> SHAPE_BRIEF_REVIEW
        self.project.transition_journey(JourneyState.SHAPE_BRIEF_REVIEW)

        logger.info("Brief generation complete: %d chars", len(self.brief_content))

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
