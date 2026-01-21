"""Dialogue data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class MessageRole(Enum):
    """Role of a message in a dialogue."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    """Immutable message in a dialogue."""

    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "id": self.id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """Create from dictionary."""
        return cls(
            role=MessageRole(data["role"]),
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            id=data["id"],
            metadata=data.get("metadata", {}),
        )


@dataclass
class DialogueHistory:
    """Container for conversation messages with persistence hooks."""

    messages: list[Message] = field(default_factory=list)
    phase: str = ""
    session_id: str = field(default_factory=lambda: str(uuid4()))

    def add_message(self, role: MessageRole, content: str, **metadata: Any) -> Message:
        """Add a message to the history."""
        msg = Message(role=role, content=content, metadata=metadata)
        self.messages.append(msg)
        return msg

    def to_api_format(self) -> list[dict[str, str]]:
        """Convert to Anthropic API message format."""
        return [
            {"role": m.role.value, "content": m.content}
            for m in self.messages
            if m.role != MessageRole.SYSTEM
        ]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "messages": [m.to_dict() for m in self.messages],
            "phase": self.phase,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DialogueHistory":
        """Create from dictionary."""
        history = cls(
            phase=data["phase"],
            session_id=data["session_id"],
        )
        history.messages = [Message.from_dict(m) for m in data["messages"]]
        return history
