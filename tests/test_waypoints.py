"""Tests for waypoints."""
from __future__ import annotations

from waypoints.models.dialogue import DialogueHistory, Message, MessageRole


class TestMessageRole:
    """Tests for MessageRole enum."""

    def test_user_role(self) -> None:
        assert MessageRole.USER.value == "user"

    def test_assistant_role(self) -> None:
        assert MessageRole.ASSISTANT.value == "assistant"

    def test_system_role(self) -> None:
        assert MessageRole.SYSTEM.value == "system"


class TestMessage:
    """Tests for Message dataclass."""

    def test_create_message(self) -> None:
        msg = Message(role=MessageRole.USER, content="Hello")
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello"
        assert msg.id is not None
        assert msg.timestamp is not None

    def test_message_to_dict(self) -> None:
        msg = Message(role=MessageRole.USER, content="Test")
        data = msg.to_dict()
        assert data["role"] == "user"
        assert data["content"] == "Test"
        assert "timestamp" in data
        assert "id" in data

    def test_message_from_dict(self) -> None:
        original = Message(role=MessageRole.ASSISTANT, content="Response")
        data = original.to_dict()
        restored = Message.from_dict(data)
        assert restored.role == original.role
        assert restored.content == original.content
        assert restored.id == original.id


class TestDialogueHistory:
    """Tests for DialogueHistory."""

    def test_create_empty_history(self) -> None:
        history = DialogueHistory(phase="test")
        assert history.phase == "test"
        assert len(history.messages) == 0
        assert history.session_id is not None

    def test_add_message(self) -> None:
        history = DialogueHistory()
        msg = history.add_message(MessageRole.USER, "Hello")
        assert len(history.messages) == 1
        assert msg.content == "Hello"
        assert msg.role == MessageRole.USER

    def test_to_api_format(self) -> None:
        history = DialogueHistory()
        history.add_message(MessageRole.USER, "Question")
        history.add_message(MessageRole.ASSISTANT, "Answer")
        history.add_message(MessageRole.SYSTEM, "System note")  # Should be excluded

        api_format = history.to_api_format()
        assert len(api_format) == 2  # System message excluded
        assert api_format[0] == {"role": "user", "content": "Question"}
        assert api_format[1] == {"role": "assistant", "content": "Answer"}

    def test_serialization_roundtrip(self) -> None:
        history = DialogueHistory(phase="shape")
        history.add_message(MessageRole.USER, "Test message")
        history.add_message(MessageRole.ASSISTANT, "Test response")

        data = history.to_dict()
        restored = DialogueHistory.from_dict(data)

        assert restored.phase == history.phase
        assert restored.session_id == history.session_id
        assert len(restored.messages) == len(history.messages)
        assert restored.messages[0].content == "Test message"
