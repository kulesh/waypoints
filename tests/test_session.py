"""Unit tests for session persistence.

Tests for SessionWriter and SessionReader.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from waypoints.models.dialogue import Message, MessageRole
from waypoints.models.session import SessionReader, SessionWriter


class MockProject:
    """Mock project for testing."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.slug = "test-project"

    def get_path(self) -> Path:
        return self._path

    def get_sessions_path(self) -> Path:
        sessions = self._path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        return sessions


@pytest.fixture
def mock_project(tmp_path: Path) -> MockProject:
    """Create a mock project for testing."""
    return MockProject(tmp_path)


class TestSessionWriter:
    """Tests for SessionWriter."""

    def test_writer_creates_header(self, mock_project: MockProject) -> None:
        """Writer creates file with header on init."""
        writer = SessionWriter(mock_project, "ideation", "session-123")

        assert writer.file_path.exists()

        with open(writer.file_path, encoding="utf-8") as f:
            header_line = f.readline()
            header = json.loads(header_line)

        assert header["_schema"] == "session"
        assert header["_version"] == "1.0"
        assert header["session_id"] == "session-123"
        assert header["phase"] == "ideation"
        assert header["project_slug"] == "test-project"
        assert "created_at" in header

    def test_writer_file_naming(self, mock_project: MockProject) -> None:
        """Writer generates correct file path."""
        writer = SessionWriter(mock_project, "shape", "session-456")

        # File should be in sessions directory
        assert writer.file_path.parent == mock_project.get_sessions_path()

        # Filename should contain phase prefix
        assert writer.file_path.name.startswith("shape-")
        assert writer.file_path.suffix == ".jsonl"

    def test_append_message(self, mock_project: MockProject) -> None:
        """Append message to session file."""
        writer = SessionWriter(mock_project, "ideation", "session-789")

        msg = Message(
            role=MessageRole.USER,
            content="Hello world",
            id="msg-1",
        )
        writer.append_message(msg)

        entries = _read_jsonl_entries(writer.file_path)

        assert len(entries) == 2  # header + message
        assert entries[1]["role"] == "user"
        assert entries[1]["content"] == "Hello world"
        assert entries[1]["id"] == "msg-1"

    def test_append_multiple_messages(self, mock_project: MockProject) -> None:
        """Append multiple messages to session file."""
        writer = SessionWriter(mock_project, "ideation", "session-abc")

        msg1 = Message(role=MessageRole.USER, content="First message", id="msg-1")
        msg2 = Message(role=MessageRole.ASSISTANT, content="Second message", id="msg-2")
        msg3 = Message(role=MessageRole.USER, content="Third message", id="msg-3")

        writer.append_message(msg1)
        writer.append_message(msg2)
        writer.append_message(msg3)

        entries = _read_jsonl_entries(writer.file_path)

        assert len(entries) == 4  # header + 3 messages
        assert entries[1]["role"] == "user"
        assert entries[2]["role"] == "assistant"
        assert entries[3]["role"] == "user"

    def test_append_message_with_metadata(self, mock_project: MockProject) -> None:
        """Append message with metadata."""
        writer = SessionWriter(mock_project, "ideation", "session-meta")

        msg = Message(
            role=MessageRole.ASSISTANT,
            content="Response with metadata",
            id="msg-meta",
            metadata={"tokens": 100, "model": "claude-3"},
        )
        writer.append_message(msg)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["metadata"] == {"tokens": 100, "model": "claude-3"}

    def test_creates_sessions_directory(self, tmp_path: Path) -> None:
        """Writer creates sessions directory if it doesn't exist."""

        class MinimalProject:
            slug = "minimal"

            def get_sessions_path(self) -> Path:
                return tmp_path / "nested" / "sessions"

        project = MinimalProject()
        writer = SessionWriter(project, "test", "session-dir")

        assert writer.file_path.exists()
        assert (tmp_path / "nested" / "sessions").exists()


class TestSessionReader:
    """Tests for SessionReader."""

    def test_load_session(self, mock_project: MockProject) -> None:
        """Load session from file."""
        writer = SessionWriter(mock_project, "ideation", "session-load")

        msg1 = Message(role=MessageRole.USER, content="User says", id="m1")
        msg2 = Message(role=MessageRole.ASSISTANT, content="AI responds", id="m2")
        writer.append_message(msg1)
        writer.append_message(msg2)

        history = SessionReader.load(writer.file_path)

        assert history.session_id == "session-load"
        assert history.phase == "ideation"
        assert len(history.messages) == 2
        assert history.messages[0].content == "User says"
        assert history.messages[1].content == "AI responds"

    def test_load_empty_session(self, mock_project: MockProject) -> None:
        """Load session with no messages."""
        writer = SessionWriter(mock_project, "empty", "session-empty")

        history = SessionReader.load(writer.file_path)

        assert history.session_id == "session-empty"
        assert history.phase == "empty"
        assert len(history.messages) == 0

    def test_load_preserves_message_data(self, mock_project: MockProject) -> None:
        """Load preserves all message fields."""
        writer = SessionWriter(mock_project, "test", "session-fields")

        msg = Message(
            role=MessageRole.USER,
            content="Test content",
            id="specific-id",
            metadata={"key": "value"},
        )
        writer.append_message(msg)

        history = SessionReader.load(writer.file_path)

        loaded_msg = history.messages[0]
        assert loaded_msg.role == MessageRole.USER
        assert loaded_msg.content == "Test content"
        assert loaded_msg.id == "specific-id"
        assert loaded_msg.metadata == {"key": "value"}

    def test_list_sessions(self, mock_project: MockProject) -> None:
        """List session files for project."""
        # Create multiple sessions with different phases to get unique filenames
        # (timestamp is only to seconds, so same-phase sessions within 1s collide)
        SessionWriter(mock_project, "ideation", "s1")
        SessionWriter(mock_project, "shape", "s2")
        SessionWriter(mock_project, "chart", "s3")

        sessions = SessionReader.list_sessions(mock_project)

        assert len(sessions) == 3

    def test_list_sessions_filtered_by_phase(self, mock_project: MockProject) -> None:
        """List sessions filtered by phase."""
        # Use 1+ second delays to get unique timestamps for same phase
        SessionWriter(mock_project, "ideation", "s1")
        time.sleep(1.1)
        SessionWriter(mock_project, "shape", "s2")
        time.sleep(1.1)
        SessionWriter(mock_project, "ideation", "s3")

        ideation_sessions = SessionReader.list_sessions(mock_project, phase="ideation")
        shape_sessions = SessionReader.list_sessions(mock_project, phase="shape")

        assert len(ideation_sessions) == 2
        assert len(shape_sessions) == 1

    def test_list_sessions_empty(self, mock_project: MockProject) -> None:
        """List returns empty for project with no sessions."""
        sessions = SessionReader.list_sessions(mock_project)
        assert sessions == []

    def test_list_sessions_nonexistent_phase(self, mock_project: MockProject) -> None:
        """List returns empty for nonexistent phase."""
        SessionWriter(mock_project, "ideation", "s1")

        sessions = SessionReader.list_sessions(mock_project, phase="nonexistent")
        assert sessions == []

    def test_list_sessions_sorted_by_mtime(self, mock_project: MockProject) -> None:
        """Sessions are sorted by modification time (newest first)."""
        # Use different phases to avoid filename collision
        writer1 = SessionWriter(mock_project, "alpha", "s1")
        writer2 = SessionWriter(mock_project, "beta", "s2")
        writer3 = SessionWriter(mock_project, "gamma", "s3")

        # Manually update mtimes to control ordering
        os.utime(writer1.file_path, (100, 100))  # Oldest
        os.utime(writer2.file_path, (200, 200))  # Middle
        os.utime(writer3.file_path, (300, 300))  # Newest

        sessions = SessionReader.list_sessions(mock_project)

        # Newest should be first
        assert sessions[0] == writer3.file_path
        assert sessions[1] == writer2.file_path
        assert sessions[2] == writer1.file_path

    def test_load_latest(self, mock_project: MockProject) -> None:
        """Load most recent session."""
        # Use different phases to get different filenames
        writer1 = SessionWriter(mock_project, "alpha", "old-session")
        msg1 = Message(role=MessageRole.USER, content="Old", id="m1")
        writer1.append_message(msg1)

        writer2 = SessionWriter(mock_project, "beta", "new-session")
        msg2 = Message(role=MessageRole.USER, content="New", id="m2")
        writer2.append_message(msg2)

        # Set mtimes to control ordering
        os.utime(writer1.file_path, (100, 100))
        os.utime(writer2.file_path, (200, 200))

        history = SessionReader.load_latest(mock_project)

        assert history is not None
        assert history.session_id == "new-session"
        assert history.messages[0].content == "New"

    def test_load_latest_with_phase(self, mock_project: MockProject) -> None:
        """Load most recent session for specific phase."""
        # Create sessions for different phases
        writer1 = SessionWriter(mock_project, "shape", "shape-session")
        msg1 = Message(role=MessageRole.USER, content="Shape", id="m1")
        writer1.append_message(msg1)

        writer2 = SessionWriter(mock_project, "ideation", "ideation-session")
        msg2 = Message(role=MessageRole.USER, content="Ideation", id="m2")
        writer2.append_message(msg2)

        # Make ideation newer by mtime
        os.utime(writer1.file_path, (100, 100))
        os.utime(writer2.file_path, (200, 200))

        # Load latest for shape phase (should still get shape-session)
        history = SessionReader.load_latest(mock_project, phase="shape")

        assert history is not None
        assert history.session_id == "shape-session"

    def test_load_latest_none(self, mock_project: MockProject) -> None:
        """Load latest returns None when no sessions exist."""
        history = SessionReader.load_latest(mock_project)
        assert history is None

    def test_load_latest_none_for_phase(self, mock_project: MockProject) -> None:
        """Load latest returns None when no sessions for phase."""
        SessionWriter(mock_project, "ideation", "s1")

        history = SessionReader.load_latest(mock_project, phase="nonexistent")
        assert history is None


class TestSessionRoundtrip:
    """Integration tests for session write/read roundtrip."""

    def test_full_conversation_roundtrip(self, mock_project: MockProject) -> None:
        """Full conversation survives roundtrip."""
        writer = SessionWriter(mock_project, "test", "roundtrip-session")

        # Simulate a full conversation
        messages = [
            Message(role=MessageRole.SYSTEM, content="System prompt", id="sys"),
            Message(role=MessageRole.USER, content="User question", id="u1"),
            Message(
                role=MessageRole.ASSISTANT,
                content="AI answer",
                id="a1",
                metadata={"tokens": 50},
            ),
            Message(role=MessageRole.USER, content="Follow up", id="u2"),
            Message(
                role=MessageRole.ASSISTANT,
                content="Final response",
                id="a2",
                metadata={"tokens": 75},
            ),
        ]

        for msg in messages:
            writer.append_message(msg)

        # Load and verify
        history = SessionReader.load(writer.file_path)

        assert len(history.messages) == 5
        for i, msg in enumerate(messages):
            loaded = history.messages[i]
            assert loaded.role == msg.role
            assert loaded.content == msg.content
            assert loaded.id == msg.id
            assert loaded.metadata == msg.metadata


def _read_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    """Helper to read all entries from a JSONL file."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries
