"""Unit tests for execution log persistence.

Tests for ExecutionEntry, ExecutionLog, ExecutionLogWriter, and ExecutionLogReader.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoints.fly.execution_log import (
    ExecutionEntry,
    ExecutionLog,
    ExecutionLogReader,
    ExecutionLogWriter,
)
from waypoints.models.waypoint import Waypoint


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


@pytest.fixture
def mock_waypoint() -> Waypoint:
    """Create a mock waypoint for testing."""
    return Waypoint(
        id="WP-1",
        title="Test Waypoint",
        objective="Test objective",
        acceptance_criteria=["Criterion 1", "Criterion 2"],
    )


class TestExecutionEntry:
    """Tests for ExecutionEntry dataclass."""

    def test_create_minimal_entry(self) -> None:
        """Create entry with required fields only."""
        entry = ExecutionEntry(entry_type="output", content="Test content")

        assert entry.entry_type == "output"
        assert entry.content == "Test content"
        assert entry.iteration == 0
        assert entry.metadata == {}
        assert isinstance(entry.timestamp, datetime)

    def test_create_full_entry(self) -> None:
        """Create entry with all fields."""
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        metadata = {"key": "value", "count": 42}

        entry = ExecutionEntry(
            entry_type="tool_call",
            content="tool:write_file",
            timestamp=ts,
            iteration=3,
            metadata=metadata,
        )

        assert entry.entry_type == "tool_call"
        assert entry.content == "tool:write_file"
        assert entry.timestamp == ts
        assert entry.iteration == 3
        assert entry.metadata == {"key": "value", "count": 42}

    def test_to_dict(self) -> None:
        """Serialize entry to dictionary."""
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        entry = ExecutionEntry(
            entry_type="error",
            content="Something went wrong",
            timestamp=ts,
            iteration=2,
            metadata={"error_code": 500},
        )

        data = entry.to_dict()

        assert data["entry_type"] == "error"
        assert data["content"] == "Something went wrong"
        assert data["timestamp"] == ts.isoformat()
        assert data["iteration"] == 2
        assert data["metadata"] == {"error_code": 500}

    def test_from_dict(self) -> None:
        """Deserialize entry from dictionary."""
        data = {
            "entry_type": "iteration",
            "content": "Starting iteration 5",
            "timestamp": "2026-01-15T10:30:00+00:00",
            "iteration": 5,
            "metadata": {"prompt_tokens": 100},
        }

        entry = ExecutionEntry.from_dict(data)

        assert entry.entry_type == "iteration"
        assert entry.content == "Starting iteration 5"
        assert entry.iteration == 5
        assert entry.metadata == {"prompt_tokens": 100}

    def test_from_dict_minimal(self) -> None:
        """Deserialize entry with only required fields."""
        data = {
            "entry_type": "output",
            "content": "Response text",
            "timestamp": "2026-01-15T10:30:00",
        }

        entry = ExecutionEntry.from_dict(data)

        assert entry.entry_type == "output"
        assert entry.content == "Response text"
        assert entry.iteration == 0
        assert entry.metadata == {}

    def test_serialization_roundtrip(self) -> None:
        """Entry survives serialization and deserialization."""
        original = ExecutionEntry(
            entry_type="result",
            content="Execution complete",
            iteration=10,
            metadata={"result": "success", "files_changed": 3},
        )

        data = original.to_dict()
        restored = ExecutionEntry.from_dict(data)

        assert restored.entry_type == original.entry_type
        assert restored.content == original.content
        assert restored.iteration == original.iteration
        assert restored.metadata == original.metadata


class TestExecutionLog:
    """Tests for ExecutionLog dataclass."""

    def test_create_minimal_log(self) -> None:
        """Create log with required fields only."""
        log = ExecutionLog(waypoint_id="WP-1", waypoint_title="Test")

        assert log.waypoint_id == "WP-1"
        assert log.waypoint_title == "Test"
        assert log.entries == []
        assert log.execution_id  # UUID generated
        assert isinstance(log.started_at, datetime)
        assert log.completed_at is None
        assert log.result is None
        assert log.total_cost_usd == 0.0

    def test_add_entry(self) -> None:
        """Add entry to log."""
        log = ExecutionLog(waypoint_id="WP-1", waypoint_title="Test")

        entry = log.add_entry(
            entry_type="output",
            content="Generated response",
            iteration=1,
            tokens=150,
        )

        assert len(log.entries) == 1
        assert entry.entry_type == "output"
        assert entry.content == "Generated response"
        assert entry.iteration == 1
        assert entry.metadata == {"tokens": 150}

    def test_add_multiple_entries(self) -> None:
        """Add multiple entries to log."""
        log = ExecutionLog(waypoint_id="WP-1", waypoint_title="Test")

        log.add_entry(entry_type="iteration", content="Start", iteration=1)
        log.add_entry(entry_type="output", content="Response", iteration=1)
        log.add_entry(entry_type="iteration", content="Start", iteration=2)
        log.add_entry(entry_type="output", content="Response", iteration=2)

        assert len(log.entries) == 4

    def test_to_dict(self) -> None:
        """Serialize log to dictionary."""
        started = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        completed = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)

        log = ExecutionLog(
            waypoint_id="WP-1",
            waypoint_title="Test Task",
            execution_id="exec-123",
            started_at=started,
            completed_at=completed,
            result="success",
            total_cost_usd=0.25,
        )
        log.add_entry(entry_type="output", content="Done", iteration=1)

        data = log.to_dict()

        assert data["waypoint_id"] == "WP-1"
        assert data["waypoint_title"] == "Test Task"
        assert data["execution_id"] == "exec-123"
        assert data["started_at"] == started.isoformat()
        assert data["completed_at"] == completed.isoformat()
        assert data["result"] == "success"
        assert data["total_cost_usd"] == 0.25
        assert len(data["entries"]) == 1

    def test_to_dict_incomplete(self) -> None:
        """Serialize incomplete log (no completion)."""
        log = ExecutionLog(waypoint_id="WP-1", waypoint_title="In Progress")

        data = log.to_dict()

        assert data["completed_at"] is None
        assert data["result"] is None

    def test_from_dict(self) -> None:
        """Deserialize log from dictionary."""
        data = {
            "waypoint_id": "WP-2",
            "waypoint_title": "Loaded Task",
            "execution_id": "exec-456",
            "started_at": "2026-01-15T11:00:00+00:00",
            "completed_at": "2026-01-15T11:30:00+00:00",
            "result": "failed",
            "total_cost_usd": 0.50,
            "entries": [
                {
                    "entry_type": "error",
                    "content": "Failed",
                    "timestamp": "2026-01-15T11:30:00+00:00",
                    "iteration": 5,
                    "metadata": {},
                }
            ],
        }

        log = ExecutionLog.from_dict(data)

        assert log.waypoint_id == "WP-2"
        assert log.waypoint_title == "Loaded Task"
        assert log.execution_id == "exec-456"
        assert log.result == "failed"
        assert log.total_cost_usd == 0.50
        assert len(log.entries) == 1
        assert log.entries[0].entry_type == "error"

    def test_from_dict_no_entries(self) -> None:
        """Deserialize log without entries."""
        data = {
            "waypoint_id": "WP-3",
            "waypoint_title": "Empty Log",
            "execution_id": "exec-789",
            "started_at": "2026-01-15T12:00:00+00:00",
            "completed_at": None,
            "result": None,
        }

        log = ExecutionLog.from_dict(data)

        assert log.entries == []
        assert log.completed_at is None

    def test_serialization_roundtrip(self) -> None:
        """Log survives serialization and deserialization."""
        original = ExecutionLog(
            waypoint_id="WP-1",
            waypoint_title="Roundtrip Test",
            result="success",
            total_cost_usd=1.25,
        )
        original.add_entry(entry_type="output", content="Test", iteration=1)
        original.add_entry(entry_type="result", content="Done", iteration=2)

        data = original.to_dict()
        restored = ExecutionLog.from_dict(data)

        assert restored.waypoint_id == original.waypoint_id
        assert restored.waypoint_title == original.waypoint_title
        assert restored.execution_id == original.execution_id
        assert restored.result == original.result
        assert restored.total_cost_usd == original.total_cost_usd
        assert len(restored.entries) == len(original.entries)


class TestExecutionLogWriter:
    """Tests for ExecutionLogWriter."""

    def test_writer_creates_header(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Writer creates file with header on init."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)

        assert writer.file_path.exists()

        with open(writer.file_path, encoding="utf-8") as f:
            header_line = f.readline()
            header = json.loads(header_line)

        assert header["type"] == "header"
        assert header["_schema"] == "execution_log"
        assert header["_version"] == "1.0"
        assert header["waypoint_id"] == "WP-1"
        assert header["waypoint_title"] == "Test Waypoint"
        assert header["project_slug"] == "test-project"

    def test_writer_file_naming(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Writer generates correct file path."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)

        # File should be in sessions/fly/ directory
        assert writer.file_path.parent == mock_project.get_sessions_path() / "fly"

        # Filename should contain waypoint ID prefix
        assert writer.file_path.name.startswith("wp1-")
        assert writer.file_path.suffix == ".jsonl"

    def test_log_iteration_start(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log iteration start event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_iteration_start(1, "Test prompt")

        entries = _read_jsonl_entries(writer.file_path)

        assert len(entries) == 2  # header + iteration_start
        assert entries[1]["type"] == "iteration_start"
        assert entries[1]["iteration"] == 1
        assert entries[1]["prompt"] == "Test prompt"

    def test_log_output(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log output event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_output(1, "Generated text", criteria_completed={0, 2})

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "output"
        assert entries[1]["iteration"] == 1
        assert entries[1]["content"] == "Generated text"
        assert entries[1]["criteria_completed"] == [0, 2]

    def test_log_output_no_criteria(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log output without criteria."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_output(1, "Just output")

        entries = _read_jsonl_entries(writer.file_path)

        assert "criteria_completed" not in entries[1]

    def test_log_tool_call(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log tool call event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_tool_call(
            iteration=2,
            tool_name="write_file",
            tool_input={"path": "/test.py", "content": "code"},
            tool_output="File written",
        )

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "tool_call"
        assert entries[1]["iteration"] == 2
        assert entries[1]["tool_name"] == "write_file"
        assert entries[1]["tool_input"] == {"path": "/test.py", "content": "code"}
        assert entries[1]["tool_output"] == "File written"

    def test_log_iteration_end(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log iteration end with cost tracking."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_iteration_end(1, cost_usd=0.05)
        writer.log_iteration_end(2, cost_usd=0.03)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "iteration_end"
        assert entries[1]["cost_usd"] == 0.05
        assert entries[1]["cumulative_cost_usd"] == 0.05

        assert entries[2]["cost_usd"] == 0.03
        assert entries[2]["cumulative_cost_usd"] == 0.08
        assert writer.total_cost_usd == 0.08

    def test_log_error(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log error event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_error(3, "API timeout")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "error"
        assert entries[1]["iteration"] == 3
        assert entries[1]["error"] == "API timeout"

    def test_log_completion(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log completion event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.total_cost_usd = 0.15
        writer.log_completion("success")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "completion"
        assert entries[1]["result"] == "success"
        assert entries[1]["total_cost_usd"] == 0.15
        assert "duration_seconds" in entries[1]

    def test_log_intervention_needed(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log intervention needed event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_intervention_needed(5, "user_guidance", "Unclear requirements")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "intervention_needed"
        assert entries[1]["intervention_type"] == "user_guidance"
        assert entries[1]["reason"] == "Unclear requirements"

    def test_log_intervention_resolved(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log intervention resolved event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_intervention_resolved("continue", guidance="Keep going")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "intervention_resolved"
        assert entries[1]["action"] == "continue"
        assert entries[1]["params"] == {"guidance": "Keep going"}

    def test_log_state_transition(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log state transition event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_state_transition("running", "paused", "User requested")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "state_transition"
        assert entries[1]["from_state"] == "running"
        assert entries[1]["to_state"] == "paused"
        assert entries[1]["reason"] == "User requested"

    def test_log_receipt_validated(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log receipt validation event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_receipt_validated("/receipts/wp1.md", True, "All criteria met")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "receipt_validated"
        assert entries[1]["path"] == "/receipts/wp1.md"
        assert entries[1]["valid"] is True
        assert entries[1]["message"] == "All criteria met"

    def test_log_git_commit(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log git commit event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_git_commit(True, commit_hash="abc123", message="feat: add feature")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "git_commit"
        assert entries[1]["success"] is True
        assert entries[1]["commit_hash"] == "abc123"
        assert entries[1]["message"] == "feat: add feature"

    def test_log_pause_resume(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log pause and resume events."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_pause()
        writer.log_resume()

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "pause"
        assert entries[2]["type"] == "resume"

    def test_log_security_violation(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log security violation event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_security_violation(7, "Attempted file access outside project")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "security_violation"
        assert entries[1]["iteration"] == 7
        assert entries[1]["details"] == "Attempted file access outside project"

    def test_log_completion_detected(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log completion detected event."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_completion_detected(10)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "completion_detected"
        assert entries[1]["iteration"] == 10

    def test_log_finalize_phases(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Log finalize phase events."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_finalize_start()
        writer.log_finalize_output("Verifying receipt...")
        writer.log_finalize_tool_call(
            "read_file", {"path": "/receipt.md"}, "Receipt content"
        )
        writer.log_finalize_end(cost_usd=0.02)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[1]["type"] == "finalize_start"
        assert entries[2]["type"] == "finalize_output"
        assert entries[2]["content"] == "Verifying receipt..."
        assert entries[3]["type"] == "finalize_tool_call"
        assert entries[4]["type"] == "finalize_end"
        assert entries[4]["cost_usd"] == 0.02

    def test_full_execution_flow(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Simulate a complete execution flow."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)

        # Iteration 1
        writer.log_iteration_start(1, "Initial prompt")
        writer.log_output(1, "First response", criteria_completed={0})
        writer.log_tool_call(1, "write_file", {"path": "test.py"}, "OK")
        writer.log_iteration_end(1, cost_usd=0.05)

        # Iteration 2
        writer.log_iteration_start(2, "Continue")
        writer.log_output(2, "Second response", criteria_completed={1})
        writer.log_iteration_end(2, cost_usd=0.03)

        # Finalize
        writer.log_finalize_start()
        writer.log_finalize_end(cost_usd=0.01)

        # Complete
        writer.log_completion("success")

        entries = _read_jsonl_entries(writer.file_path)

        # header + 4 iter1 + 3 iter2 + 2 finalize + 1 completion = 11
        assert len(entries) == 11
        assert writer.total_cost_usd == 0.09


class TestExecutionLogReader:
    """Tests for ExecutionLogReader."""

    def test_load_log(self, mock_project: MockProject, mock_waypoint: Waypoint) -> None:
        """Load execution log from file."""
        # Create a log file
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_iteration_start(1, "Test")
        writer.log_output(1, "Response")
        writer.log_completion("success")

        # Load it
        log = ExecutionLogReader.load(writer.file_path)

        assert log.waypoint_id == "WP-1"
        assert log.waypoint_title == "Test Waypoint"
        assert log.result == "success"
        assert len(log.entries) >= 2

    def test_load_incomplete_log(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Load log without completion."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_iteration_start(1, "Test")

        log = ExecutionLogReader.load(writer.file_path)

        assert log.result is None
        assert log.completed_at is None

    def test_load_file_without_header(self, tmp_path: Path) -> None:
        """Load raises error for file without proper header."""
        invalid_file = tmp_path / "invalid.jsonl"
        # File with no header entry results in ValueError
        invalid_file.write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="Invalid execution log"):
            ExecutionLogReader.load(invalid_file)

    def test_load_file_with_malformed_entry(self, tmp_path: Path) -> None:
        """Load raises error for file with malformed entries."""
        invalid_file = tmp_path / "invalid.jsonl"
        # Entry without timestamp causes KeyError
        invalid_file.write_text(
            '{"type": "not_header", "content": "test"}\n', encoding="utf-8"
        )

        with pytest.raises(KeyError):
            ExecutionLogReader.load(invalid_file)

    def test_list_logs(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """List execution logs for project."""
        # Create multiple log files
        writer1 = ExecutionLogWriter(mock_project, mock_waypoint)
        writer1.log_completion("success")

        # Create another waypoint
        wp2 = Waypoint(id="WP-2", title="Second", objective="Second objective")
        writer2 = ExecutionLogWriter(mock_project, wp2)
        writer2.log_completion("failed")

        logs = ExecutionLogReader.list_logs(mock_project)

        assert len(logs) == 2

    def test_list_logs_filtered(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """List logs filtered by waypoint ID."""
        writer1 = ExecutionLogWriter(mock_project, mock_waypoint)
        writer1.log_completion("success")

        wp2 = Waypoint(id="WP-2", title="Second", objective="Second")
        writer2 = ExecutionLogWriter(mock_project, wp2)
        writer2.log_completion("success")

        # Filter by WP-1
        logs = ExecutionLogReader.list_logs(mock_project, waypoint_id="WP-1")

        assert len(logs) == 1
        assert "wp1-" in logs[0].name

    def test_list_logs_empty(self, mock_project: MockProject) -> None:
        """List returns empty when no logs exist."""
        logs = ExecutionLogReader.list_logs(mock_project)
        assert logs == []

    def test_load_latest(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Load most recent log."""
        # Create first log
        writer1 = ExecutionLogWriter(mock_project, mock_waypoint)
        writer1.log_completion("first")

        # Small delay to ensure different timestamps
        import time

        time.sleep(0.01)

        # Create second log
        writer2 = ExecutionLogWriter(mock_project, mock_waypoint)
        writer2.log_completion("second")

        log = ExecutionLogReader.load_latest(mock_project, waypoint_id="WP-1")

        assert log is not None
        assert log.result == "second"

    def test_load_latest_none(self, mock_project: MockProject) -> None:
        """Load latest returns None when no logs exist."""
        log = ExecutionLogReader.load_latest(mock_project, waypoint_id="WP-999")
        assert log is None

    def test_get_completed_criteria(
        self, mock_project: MockProject, mock_waypoint: Waypoint
    ) -> None:
        """Get completed criteria from log."""
        writer = ExecutionLogWriter(mock_project, mock_waypoint)
        writer.log_output(1, "First", criteria_completed={0, 1})
        writer.log_output(2, "Second", criteria_completed={2})
        writer.log_completion("success")

        criteria = ExecutionLogReader.get_completed_criteria(
            mock_project, waypoint_id="WP-1"
        )

        assert criteria == {0, 1, 2}

    def test_get_completed_criteria_empty(self, mock_project: MockProject) -> None:
        """Get criteria returns empty set when no logs."""
        criteria = ExecutionLogReader.get_completed_criteria(
            mock_project, waypoint_id="WP-999"
        )
        assert criteria == set()


def _read_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    """Helper to read all entries from a JSONL file."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries
