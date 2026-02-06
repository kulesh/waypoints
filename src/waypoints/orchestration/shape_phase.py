"""SHAPE phase delegate — Q&A dialogue, brief, and spec generation.

Owns the business logic for the ideation phase: conducting Q&A
dialogues to refine ideas, generating idea briefs, and producing
product specifications from briefs.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from waypoints.llm.client import ChatClient, StreamChunk
from waypoints.llm.prompts import (
    BRIEF_GENERATION_PROMPT,
    BRIEF_SUMMARY_PROMPT,
    BRIEF_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    SPEC_GENERATION_PROMPT,
    SPEC_SUMMARY_PROMPT,
    SPEC_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
)
from waypoints.models import DialogueHistory, MessageRole, SessionWriter
from waypoints.orchestration.types import ChunkCallback

if TYPE_CHECKING:
    from waypoints.orchestration.coordinator import JourneyCoordinator

logger = logging.getLogger(__name__)


class ShapePhase:
    """Q&A dialogue management, brief generation, and spec generation."""

    def __init__(self, coordinator: "JourneyCoordinator") -> None:
        self._coord = coordinator

    # ─── Q&A Dialogue ─────────────────────────────────────────────────

    @property
    def dialogue_history(self) -> DialogueHistory | None:
        """Get the current dialogue history."""
        return self._coord._dialogue_history

    def start_qa_dialogue(
        self,
        idea: str,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Start Q&A dialogue with first question.

        Initializes dialogue history, sends idea to LLM,
        and returns the first question.

        Args:
            idea: The user's initial idea
            on_chunk: Optional callback for streaming chunks

        Returns:
            The LLM's first question/response
        """
        # Initialize dialogue state
        self._coord._idea = idea
        self._coord._dialogue_history = DialogueHistory()
        self._coord._session_writer = SessionWriter(
            self._coord.project,
            "ideation",
            self._coord._dialogue_history.session_id,
        )

        # Create LLM client if needed
        if self._coord.llm is None:
            self._coord.llm = ChatClient(
                metrics_collector=self._coord.metrics,
                phase="ideation-qa",
            )

        # Format initial context
        initial_context = (
            f"I have an idea I'd like to refine:\n\n{idea}\n\n"
            "Please help me crystallize this idea by asking clarifying questions."
        )

        # Add to history and persist
        initial_msg = self._coord._dialogue_history.add_message(
            MessageRole.USER, initial_context
        )
        self._coord._session_writer.append_message(initial_msg)

        logger.info("Starting ideation Q&A with idea: %s", idea[:100])

        # Stream response from LLM
        response_content = ""
        for result in self._coord.llm.stream_message(
            messages=self._coord._dialogue_history.to_api_format(),
            system=QA_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                response_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save assistant response to history
        assistant_msg = self._coord._dialogue_history.add_message(
            MessageRole.ASSISTANT, response_content
        )
        self._coord._session_writer.append_message(assistant_msg)

        return response_content

    def continue_qa_dialogue(
        self,
        user_response: str,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Continue Q&A dialogue with user's response.

        Args:
            user_response: User's answer to the previous question
            on_chunk: Optional callback for streaming chunks

        Returns:
            The LLM's next question/response

        Raises:
            RuntimeError: If dialogue not started (call start_qa_dialogue first)
        """
        if self._coord._dialogue_history is None or self._coord._session_writer is None:
            raise RuntimeError("Dialogue not started. Call start_qa_dialogue first.")

        if self._coord.llm is None:
            self._coord.llm = ChatClient(
                metrics_collector=self._coord.metrics,
                phase="ideation-qa",
            )

        # Add user response to history
        user_msg = self._coord._dialogue_history.add_message(
            MessageRole.USER, user_response
        )
        self._coord._session_writer.append_message(user_msg)

        # Stream response from LLM
        response_content = ""
        for result in self._coord.llm.stream_message(
            messages=self._coord._dialogue_history.to_api_format(),
            system=QA_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                response_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save assistant response to history
        assistant_msg = self._coord._dialogue_history.add_message(
            MessageRole.ASSISTANT, response_content
        )
        self._coord._session_writer.append_message(assistant_msg)

        return response_content

    # ─── Brief & Spec Generation ──────────────────────────────────────

    def generate_idea_brief(
        self,
        history: DialogueHistory | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Generate idea brief from Q&A dialogue.

        Args:
            history: Dialogue history to use (defaults to current session)
            on_chunk: Optional callback for streaming chunks

        Returns:
            The generated idea brief content

        Raises:
            RuntimeError: If no dialogue history available
        """
        dialogue = history or self._coord._dialogue_history
        if dialogue is None:
            raise RuntimeError("No dialogue history. Run Q&A dialogue first.")

        # Create LLM client if needed
        if self._coord.llm is None:
            self._coord.llm = ChatClient(
                metrics_collector=self._coord.metrics,
                phase="idea-brief",
            )

        # Format conversation for prompt
        conversation_text = self._format_conversation(dialogue)
        prompt = BRIEF_GENERATION_PROMPT.format(conversation=conversation_text)

        logger.info("Generating idea brief from %d messages", len(dialogue.messages))

        # Stream response from LLM
        brief_content = ""
        for result in self._coord.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=BRIEF_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                brief_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save to disk
        file_path = self._save_doc("idea-brief", brief_content)
        logger.info("Saved brief to %s", file_path)

        # Generate summary in background (non-blocking for now)
        self._generate_project_summary(brief_content, "brief")

        return brief_content

    def generate_product_spec(
        self,
        brief: str,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Generate product specification from idea brief.

        Args:
            brief: The idea brief content
            on_chunk: Optional callback for streaming chunks

        Returns:
            The generated product specification content
        """
        # Create LLM client if needed
        if self._coord.llm is None:
            self._coord.llm = ChatClient(
                metrics_collector=self._coord.metrics,
                phase="product-spec",
            )

        prompt = SPEC_GENERATION_PROMPT.format(brief=brief)

        logger.info("Generating product spec from brief: %d chars", len(brief))

        # Stream response from LLM
        spec_content = ""
        for result in self._coord.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=SPEC_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                spec_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save to disk
        file_path = self._save_doc("product-spec", spec_content)
        logger.info("Saved spec to %s", file_path)

        # Generate summary
        self._generate_project_summary(spec_content, "spec")

        return spec_content

    # ─── Private Helpers ──────────────────────────────────────────────

    def _format_conversation(self, history: DialogueHistory) -> str:
        """Format dialogue history for generation prompts."""
        parts = []
        for msg in history.messages:
            role = "User" if msg.role == MessageRole.USER else "Assistant"
            parts.append(f"{role}: {msg.content}")
        return "\n\n".join(parts)

    def _save_doc(self, doc_type: str, content: str) -> Path:
        """Save document to project docs directory.

        Args:
            doc_type: Type of document (e.g., "idea-brief", "product-spec")
            content: Document content

        Returns:
            Path to saved file
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        docs_dir = self._coord.project.get_docs_path()
        docs_dir.mkdir(parents=True, exist_ok=True)
        file_path = docs_dir / f"{doc_type}-{timestamp}.md"
        file_path.write_text(content)
        return file_path

    def _generate_project_summary(self, content: str, source: str) -> None:
        """Generate and save project summary from content.

        Args:
            content: The document content to summarize
            source: Source type ("brief" or "spec")
        """
        if self._coord.llm is None:
            return

        if source == "brief":
            prompt = BRIEF_SUMMARY_PROMPT.format(brief_content=content)
        else:
            prompt = SPEC_SUMMARY_PROMPT.format(spec_content=content)

        try:
            summary = ""
            for result in self._coord.llm.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=SUMMARY_SYSTEM_PROMPT,
            ):
                if isinstance(result, StreamChunk):
                    summary += result.text

            # Clean up and save
            summary = summary.strip()
            self._coord.project.summary = summary
            self._coord.project.save()
            logger.info("Generated project summary: %d chars", len(summary))
        except Exception as e:
            logger.exception("Error generating summary: %s", e)
