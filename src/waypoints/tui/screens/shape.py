"""SHAPE phase screen for Q&A refinement."""

import logging
from typing import Any
from uuid import uuid4

from textual import work

from waypoints.llm.client import ChatClient
from waypoints.models.dialogue import MessageRole
from waypoints.tui.messages import (
    StreamingChunk,
    StreamingCompleted,
    StreamingStarted,
)
from waypoints.tui.screens.base import BaseDialogueScreen

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a product design assistant helping refine an idea into a product spec.

Your role is to ask clarifying questions one at a time to help the user
crystallize their idea. After each answer, acknowledge what you learned
and ask the next most important question.

Focus on understanding:
1. The core problem being solved
2. Who the target users are
3. Key features and capabilities
4. Technical constraints or requirements
5. Success criteria

Keep your questions focused and specific. Don't ask multiple questions at once.

When you have gathered enough information (usually after 5-10 questions),
let the user know you're ready to generate the specification."""


class ShapeScreen(BaseDialogueScreen):
    """
    SHAPE phase screen - Q&A refinement to build product spec.

    The assistant asks clarifying questions, user answers.
    """

    phase_name = "shape"
    input_hint = None

    def __init__(
        self, brief_data: dict[str, Any] | None = None, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)
        self.brief_data = brief_data or {}
        self.llm_client = ChatClient()

    def on_mount(self) -> None:
        """Initialize with first question."""
        super().on_mount()
        self._start_conversation()

    def handle_user_message(self, text: str) -> None:
        """Process user's answer to a question."""
        # Add to history
        self.history.add_message(MessageRole.USER, text)

        # Display in UI
        self.dialogue_view.add_user_message(text)

        # Trigger LLM for next question
        self._send_to_llm()

    @work(thread=True)
    def _start_conversation(self) -> None:
        """Start the conversation with an initial prompt."""
        # Create initial context message
        if self.brief_data:
            idea = self.brief_data.get("idea", "No specific idea provided yet.")
            initial_context = (
                f"I have an idea I'd like to refine into a product spec:\n\n{idea}"
            )
        else:
            initial_context = (
                "I'd like to develop a product idea. "
                "Please help me refine it into a detailed specification."
            )

        self.history.add_message(MessageRole.USER, initial_context)
        logger.info("Starting conversation with context: %s", initial_context[:100])

        # Signal streaming start
        self.app.call_from_thread(self.post_message, StreamingStarted())

        response_content = ""
        message_id = str(uuid4())

        # Stream response from Anthropic
        try:
            logger.info("Calling LLM with %d messages", len(self.history.messages))
            for chunk in self.llm_client.stream_message(
                messages=self.history.to_api_format(),
                system=SYSTEM_PROMPT,
            ):
                response_content += chunk
                self.app.call_from_thread(
                    self.post_message, StreamingChunk(chunk, message_id)
                )
            logger.info("LLM response complete: %d chars", len(response_content))
        except Exception as e:
            logger.exception("Error calling LLM: %s", e)
            response_content = f"Error: {e}"
            # Show error in UI via notification
            self.app.call_from_thread(
                self.notify, f"API Error: {e}", severity="error"
            )

        # Add to history and signal completion
        self.history.add_message(MessageRole.ASSISTANT, response_content)
        self.app.call_from_thread(
            self.post_message, StreamingCompleted(message_id, response_content)
        )

    @work(thread=True)
    def _send_to_llm(self) -> None:
        """Send conversation to LLM in background thread."""
        # Signal streaming start
        self.app.call_from_thread(self.post_message, StreamingStarted())

        response_content = ""
        message_id = str(uuid4())

        # Stream response from Anthropic
        try:
            logger.info("Sending to LLM with %d messages", len(self.history.messages))
            for chunk in self.llm_client.stream_message(
                messages=self.history.to_api_format(),
                system=SYSTEM_PROMPT,
            ):
                response_content += chunk
                self.app.call_from_thread(
                    self.post_message, StreamingChunk(chunk, message_id)
                )
            logger.info("LLM response complete: %d chars", len(response_content))
        except Exception as e:
            logger.exception("Error calling LLM: %s", e)
            response_content = f"Error: {e}"
            self.app.call_from_thread(
                self.notify, f"API Error: {e}", severity="error"
            )

        # Add to history and signal completion
        self.history.add_message(MessageRole.ASSISTANT, response_content)
        self.app.call_from_thread(
            self.post_message, StreamingCompleted(message_id, response_content)
        )

    def process_response(self, content: str) -> None:
        """Process completed LLM response."""
        # Future: parse response for structured data, detect completion
        pass
