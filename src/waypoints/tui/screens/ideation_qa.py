"""Ideation Q&A screen for refining ideas through dialogue."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Rule, Static

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import MessageRole
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.messages import (
    StreamingChunk,
    StreamingCompleted,
    StreamingStarted,
)
from waypoints.tui.screens.base import BaseDialogueScreen
from waypoints.tui.widgets.dialogue import (
    DialoguePanel,
)
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

logger = logging.getLogger(__name__)


class IdeationQAScreen(BaseDialogueScreen):
    """
    Ideation Q&A screen - Refine idea through user-controlled dialogue.

    Shows the original idea at top, Q&A dialogue below.
    User presses Ctrl+D when satisfied to generate idea brief.
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+d", "finish_ideation", "Done", show=True, priority=True),
        Binding("ctrl+b", "back", "Back", show=True),
        Binding("escape", "focus_input", "Focus Input", show=False),
    ]

    phase_name = "Ideation"
    input_hint = None

    DEFAULT_CSS = """
    IdeationQAScreen {
        overflow: hidden;
    }

    IdeationQAScreen .idea-header {
        width: 100%;
        height: auto;
        padding: 1 2 0 2;
        background: transparent;
    }

    IdeationQAScreen .idea-text {
        color: $text-muted;
    }

    IdeationQAScreen .idea-header Rule {
        color: $surface-lighten-1;
        margin: 1 0 0 0;
    }

    IdeationQAScreen ModelStatusIndicator {
        dock: top;
        layer: above;
        margin: 0 0 0 1;
        height: 1;
        width: 2;
        background: initial;
    }
    """

    def __init__(self, project: Project, idea: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.idea = idea
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

    def compose(self) -> ComposeResult:
        yield Header()
        yield ModelStatusIndicator(id="model-status")
        with Vertical(classes="idea-header"):
            yield Static(self._truncate_idea(self.idea), classes="idea-text")
            yield Rule(line_style="solid")
        yield DialoguePanel(
            input_hint=self.input_hint,
            id="dialogue-panel",
        )
        yield Footer()

    def _truncate_idea(self, idea: str, max_lines: int = 3) -> str:
        """Truncate idea for display in header."""
        lines = idea.split("\n")[:max_lines]
        result = "\n".join(lines)
        if len(idea.split("\n")) > max_lines:
            result += " ..."
        return result

    def on_mount(self) -> None:
        """Initialize Q&A with first question."""
        self.app.sub_title = f"{self.project.name} · {self.phase_name}"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Transition journey state: SPARK_ENTERING -> SHAPE_QA
        self.coordinator.transition(
            JourneyState.SHAPE_QA,
            reason="ideation_qa.start",
        )

        self._start_qa()

    def handle_user_message(self, text: str) -> None:
        """Process user's answer."""
        # Add to local history for UI display
        self.history.add_message(MessageRole.USER, text)
        self.dialogue_view.add_user_message(text)
        self._send_to_llm(text)

    def process_response(self, content: str) -> None:
        """Process completed response - just continue Q&A."""
        pass

    @work(thread=True)
    def _start_qa(self) -> None:
        """Start Q&A with the idea as context via coordinator."""
        message_id = str(uuid4())
        self.app.call_from_thread(self.post_message, StreamingStarted())

        def on_chunk(chunk: str) -> None:
            self.app.call_from_thread(
                self.post_message, StreamingChunk(chunk, message_id)
            )

        try:
            response = self.coordinator.start_qa_dialogue(
                idea=self.idea,
                on_chunk=on_chunk,
            )
            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            # Add assistant response to local history for UI
            self.history.add_message(MessageRole.ASSISTANT, response)

        except Exception as e:
            logger.exception("Error calling LLM: %s", e)
            response = f"Error: {e}"
            self.app.call_from_thread(self.notify, f"API Error: {e}", severity="error")

        self.app.call_from_thread(
            self.post_message, StreamingCompleted(message_id, response)
        )

    @work(thread=True)
    def _send_to_llm(self, user_response: str) -> None:
        """Continue Q&A dialogue via coordinator."""
        message_id = str(uuid4())
        self.app.call_from_thread(self.post_message, StreamingStarted())

        def on_chunk(chunk: str) -> None:
            self.app.call_from_thread(
                self.post_message, StreamingChunk(chunk, message_id)
            )

        try:
            response = self.coordinator.continue_qa_dialogue(
                user_response=user_response,
                on_chunk=on_chunk,
            )
            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            # Add assistant response to local history for UI
            self.history.add_message(MessageRole.ASSISTANT, response)

        except Exception as e:
            logger.exception("Error calling LLM: %s", e)
            response = f"Error: {e}"
            self.app.call_from_thread(self.notify, f"API Error: {e}", severity="error")

        self.app.call_from_thread(
            self.post_message, StreamingCompleted(message_id, response)
        )

    def action_finish_ideation(self) -> None:
        """User is satisfied - generate idea brief."""
        # Use coordinator's dialogue history (has full conversation)
        dialogue_history = self.coordinator.dialogue_history
        self.app.switch_phase(  # type: ignore
            "idea-brief",
            {
                "project": self.project,
                "idea": self.idea,
                "history": dialogue_history,
            },
        )

    def action_back(self) -> None:
        """Go back to project selection."""
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.app.switch_screen(ProjectSelectionScreen())
