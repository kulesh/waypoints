"""Custom Textual messages for inter-widget communication."""
from __future__ import annotations

from typing import Any

from textual.message import Message


class UserSubmitted(Message):
    """User submitted input from the dialogue."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class StreamingStarted(Message):
    """LLM streaming response has begun."""

    pass


class StreamingChunk(Message):
    """A chunk of streaming response received."""

    def __init__(self, chunk: str, message_id: str) -> None:
        self.chunk = chunk
        self.message_id = message_id
        super().__init__()


class StreamingCompleted(Message):
    """LLM streaming response finished."""

    def __init__(self, message_id: str, full_content: str) -> None:
        self.message_id = message_id
        self.full_content = full_content
        super().__init__()


class RightPanelUpdate(Message):
    """Request to update right panel content."""

    def __init__(self, content: Any) -> None:
        self.content = content
        super().__init__()


class PhaseTransition(Message):
    """Request to transition to a different phase."""

    def __init__(self, target_phase: str, data: dict[str, Any] | None = None) -> None:
        self.target_phase = target_phase
        self.data = data or {}
        super().__init__()
