"""Ideation Q&A screen for refining ideas through dialogue."""

import logging
from uuid import uuid4

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Rule, Static

from waypoints.llm.client import ChatClient, StreamChunk, StreamComplete
from waypoints.models import JourneyState, Project, SessionWriter
from waypoints.models.dialogue import MessageRole
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

SYSTEM_PROMPT = """\
You are a product design assistant helping crystallize an idea through dialogue.

Your role is to ask ONE clarifying question at a time to help the user refine
their idea. After each answer, briefly acknowledge what you learned, then ask
the next most important question.

Focus on understanding:
1. The core problem being solved and why it matters
2. Who the target users are and their pain points
3. Key features and capabilities needed
4. Technical constraints or preferences
5. What success looks like

Guidelines:
- Ask only ONE question per response
- Keep questions focused and specific
- Build on previous answers
- Be curious and dig deeper when answers are vague
- Don't summarize or conclude - the user will tell you when they're done

The user will press Ctrl+D when they feel the idea is sufficiently refined.
Until then, keep asking questions to deepen understanding."""


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
        background: transparent;
    }
    """

    def __init__(self, project: Project, idea: str, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.idea = idea
        self.llm_client: ChatClient | None = None
        self.session_writer = SessionWriter(
            project, "ideation", self.history.session_id
        )

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
        self.app.set_project_for_metrics(self.project)

        # Create ChatClient with metrics collector
        self.llm_client = ChatClient(
            metrics_collector=self.app.metrics_collector,
            phase="ideation-qa",
        )

        # Transition journey state: SPARK_ENTERING -> SHAPE_QA
        self.project.transition_journey(JourneyState.SHAPE_QA)

        self._start_qa()

    def handle_user_message(self, text: str) -> None:
        """Process user's answer."""
        msg = self.history.add_message(MessageRole.USER, text)
        self.session_writer.append_message(msg)
        self.dialogue_view.add_user_message(text)
        self._send_to_llm()

    def process_response(self, content: str) -> None:
        """Process completed response - just continue Q&A."""
        pass

    @work(thread=True)
    def _start_qa(self) -> None:
        """Start Q&A with the idea as context."""
        assert self.llm_client is not None, "llm_client not initialized"

        initial_context = (
            f"I have an idea I'd like to refine:\n\n{self.idea}\n\n"
            "Please help me crystallize this idea by asking clarifying questions."
        )

        initial_msg = self.history.add_message(MessageRole.USER, initial_context)
        self.session_writer.append_message(initial_msg)
        logger.info("Starting ideation Q&A with idea: %s", self.idea[:100])

        self.app.call_from_thread(self.post_message, StreamingStarted())

        response_content = ""
        message_id = str(uuid4())

        try:
            for result in self.llm_client.stream_message(
                messages=self.history.to_api_format(),
                system=SYSTEM_PROMPT,
            ):
                if isinstance(result, StreamChunk):
                    response_content += result.text
                    self.app.call_from_thread(
                        self.post_message, StreamingChunk(result.text, message_id)
                    )
                elif isinstance(result, StreamComplete):
                    # Update header cost display
                    self.app.call_from_thread(self.app.update_header_cost)
        except Exception as e:
            logger.exception("Error calling LLM: %s", e)
            response_content = f"Error: {e}"
            self.app.call_from_thread(self.notify, f"API Error: {e}", severity="error")

        assistant_msg = self.history.add_message(
            MessageRole.ASSISTANT, response_content
        )
        self.session_writer.append_message(assistant_msg)
        self.app.call_from_thread(
            self.post_message, StreamingCompleted(message_id, response_content)
        )

    @work(thread=True)
    def _send_to_llm(self) -> None:
        """Send conversation to LLM for next question."""
        assert self.llm_client is not None, "llm_client not initialized"

        self.app.call_from_thread(self.post_message, StreamingStarted())

        response_content = ""
        message_id = str(uuid4())

        try:
            for result in self.llm_client.stream_message(
                messages=self.history.to_api_format(),
                system=SYSTEM_PROMPT,
            ):
                if isinstance(result, StreamChunk):
                    response_content += result.text
                    self.app.call_from_thread(
                        self.post_message, StreamingChunk(result.text, message_id)
                    )
                elif isinstance(result, StreamComplete):
                    # Update header cost display
                    self.app.call_from_thread(self.app.update_header_cost)
        except Exception as e:
            logger.exception("Error calling LLM: %s", e)
            response_content = f"Error: {e}"
            self.app.call_from_thread(self.notify, f"API Error: {e}", severity="error")

        assistant_msg = self.history.add_message(
            MessageRole.ASSISTANT, response_content
        )
        self.session_writer.append_message(assistant_msg)
        self.app.call_from_thread(
            self.post_message, StreamingCompleted(message_id, response_content)
        )

    def action_finish_ideation(self) -> None:
        """User is satisfied - generate idea brief."""
        self.app.switch_phase(  # type: ignore
            "idea-brief",
            {
                "project": self.project,
                "idea": self.idea,
                "history": self.history,
                "from_phase": "ideation-qa",
            },
        )

    def action_back(self) -> None:
        """Go back to project selection."""
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.app.switch_screen(ProjectSelectionScreen())
