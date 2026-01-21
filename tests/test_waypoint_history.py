"""Unit tests for waypoint history tracking.

Tests for WaypointHistoryEntry, WaypointHistoryWriter, and WaypointHistoryReader.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoints.models.waypoint_history import (
    WaypointHistoryEntry,
    WaypointHistoryReader,
    WaypointHistoryWriter,
)


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


class TestWaypointHistoryEntry:
    """Tests for WaypointHistoryEntry dataclass."""

    def test_create_entry(self) -> None:
        """Create entry with all fields."""
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        data = {"waypoint_id": "WP-1", "title": "Test"}

        entry = WaypointHistoryEntry(
            event_type="added",
            timestamp=ts,
            data=data,
        )

        assert entry.event_type == "added"
        assert entry.timestamp == ts
        assert entry.data == {"waypoint_id": "WP-1", "title": "Test"}

    def test_to_dict(self) -> None:
        """Serialize entry to dictionary."""
        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        entry = WaypointHistoryEntry(
            event_type="deleted",
            timestamp=ts,
            data={"waypoint_id": "WP-2"},
        )

        result = entry.to_dict()

        assert result["event_type"] == "deleted"
        assert result["timestamp"] == ts.isoformat()
        assert result["data"] == {"waypoint_id": "WP-2"}

    def test_from_dict(self) -> None:
        """Deserialize entry from dictionary."""
        data = {
            "event_type": "updated",
            "timestamp": "2026-01-15T10:30:00+00:00",
            "data": {"before": {"title": "Old"}, "after": {"title": "New"}},
        }

        entry = WaypointHistoryEntry.from_dict(data)

        assert entry.event_type == "updated"
        assert entry.data == {"before": {"title": "Old"}, "after": {"title": "New"}}

    def test_from_dict_empty_data(self) -> None:
        """Deserialize entry with missing data field."""
        data = {
            "event_type": "pause",
            "timestamp": "2026-01-15T10:30:00",
        }

        entry = WaypointHistoryEntry.from_dict(data)

        assert entry.event_type == "pause"
        assert entry.data == {}

    def test_serialization_roundtrip(self) -> None:
        """Entry survives serialization and deserialization."""
        original = WaypointHistoryEntry(
            event_type="broken_down",
            timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
            data={"parent_id": "WP-1", "sub_waypoints": ["WP-1a", "WP-1b"]},
        )

        result = WaypointHistoryEntry.from_dict(original.to_dict())

        assert result.event_type == original.event_type
        assert result.data == original.data


class TestWaypointHistoryWriter:
    """Tests for WaypointHistoryWriter."""

    def test_writer_creates_chart_directory(self, mock_project: MockProject) -> None:
        """Writer creates chart directory if it doesn't exist."""
        writer = WaypointHistoryWriter(mock_project)

        assert writer.file_path.parent.exists()
        assert writer.file_path.parent.name == "chart"

    def test_log_generated(self, mock_project: MockProject) -> None:
        """Log initial waypoint generation."""
        writer = WaypointHistoryWriter(mock_project)

        waypoints = [
            {"id": "WP-1", "title": "First", "objective": "First objective"},
            {"id": "WP-2", "title": "Second", "objective": "Second objective"},
        ]
        writer.log_generated(waypoints)

        entries = _read_jsonl_entries(writer.file_path)

        assert len(entries) == 1
        assert entries[0]["event_type"] == "generated"
        assert entries[0]["data"]["waypoints"] == waypoints
        assert "timestamp" in entries[0]

    def test_log_added(self, mock_project: MockProject) -> None:
        """Log waypoint addition."""
        writer = WaypointHistoryWriter(mock_project)

        waypoint = {"id": "WP-3", "title": "New", "objective": "New objective"}
        writer.log_added(waypoint, insert_after="WP-2")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[0]["event_type"] == "added"
        assert entries[0]["data"]["waypoint"] == waypoint
        assert entries[0]["data"]["insert_after"] == "WP-2"

    def test_log_added_at_end(self, mock_project: MockProject) -> None:
        """Log waypoint addition at end (no insert_after)."""
        writer = WaypointHistoryWriter(mock_project)

        waypoint = {"id": "WP-4", "title": "Last", "objective": "Last objective"}
        writer.log_added(waypoint)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[0]["data"]["insert_after"] is None

    def test_log_deleted(self, mock_project: MockProject) -> None:
        """Log waypoint deletion."""
        writer = WaypointHistoryWriter(mock_project)

        waypoint_data = {"id": "WP-5", "title": "Deleted", "objective": "Gone"}
        writer.log_deleted("WP-5", waypoint_data)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[0]["event_type"] == "deleted"
        assert entries[0]["data"]["waypoint_id"] == "WP-5"
        assert entries[0]["data"]["waypoint"] == waypoint_data

    def test_log_updated(self, mock_project: MockProject) -> None:
        """Log waypoint update."""
        writer = WaypointHistoryWriter(mock_project)

        before = {"id": "WP-6", "title": "Old Title", "objective": "Old"}
        after = {"id": "WP-6", "title": "New Title", "objective": "New"}
        writer.log_updated("WP-6", before, after)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[0]["event_type"] == "updated"
        assert entries[0]["data"]["waypoint_id"] == "WP-6"
        assert entries[0]["data"]["before"] == before
        assert entries[0]["data"]["after"] == after

    def test_log_broken_down(self, mock_project: MockProject) -> None:
        """Log waypoint breakdown."""
        writer = WaypointHistoryWriter(mock_project)

        sub_waypoints = [
            {"id": "WP-1a", "title": "Sub 1", "parent_id": "WP-1"},
            {"id": "WP-1b", "title": "Sub 2", "parent_id": "WP-1"},
        ]
        writer.log_broken_down("WP-1", sub_waypoints)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[0]["event_type"] == "broken_down"
        assert entries[0]["data"]["parent_id"] == "WP-1"
        assert entries[0]["data"]["sub_waypoints"] == sub_waypoints

    def test_log_reprioritized(self, mock_project: MockProject) -> None:
        """Log waypoint reprioritization."""
        writer = WaypointHistoryWriter(mock_project)

        previous = ["WP-1", "WP-2", "WP-3"]
        new = ["WP-2", "WP-1", "WP-3"]
        changes = [{"id": "WP-2", "reason": "Higher priority"}]

        writer.log_reprioritized(previous, new, "WP-2 moved up", changes)

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[0]["event_type"] == "reprioritized"
        assert entries[0]["data"]["previous_order"] == previous
        assert entries[0]["data"]["new_order"] == new
        assert entries[0]["data"]["rationale"] == "WP-2 moved up"
        assert entries[0]["data"]["changes"] == changes

    def test_log_reprioritized_no_changes(self, mock_project: MockProject) -> None:
        """Log reprioritization without individual changes."""
        writer = WaypointHistoryWriter(mock_project)

        writer.log_reprioritized(["WP-1", "WP-2"], ["WP-2", "WP-1"], "Reordered")

        entries = _read_jsonl_entries(writer.file_path)

        assert entries[0]["data"]["changes"] == []

    def test_multiple_events(self, mock_project: MockProject) -> None:
        """Log multiple events to same file."""
        writer = WaypointHistoryWriter(mock_project)

        # Generate initial waypoints
        writer.log_generated([{"id": "WP-1", "title": "First"}])

        # Add a waypoint
        writer.log_added({"id": "WP-2", "title": "Second"})

        # Update first waypoint
        writer.log_updated(
            "WP-1", {"title": "First"}, {"title": "First (updated)"}
        )

        # Break down second waypoint
        writer.log_broken_down("WP-2", [{"id": "WP-2a", "title": "Sub"}])

        entries = _read_jsonl_entries(writer.file_path)

        assert len(entries) == 4
        assert entries[0]["event_type"] == "generated"
        assert entries[1]["event_type"] == "added"
        assert entries[2]["event_type"] == "updated"
        assert entries[3]["event_type"] == "broken_down"


class TestWaypointHistoryReader:
    """Tests for WaypointHistoryReader."""

    def test_load_empty(self, mock_project: MockProject) -> None:
        """Load returns empty list when no history exists."""
        entries = WaypointHistoryReader.load(mock_project)
        assert entries == []

    def test_load_history(self, mock_project: MockProject) -> None:
        """Load history entries from file."""
        writer = WaypointHistoryWriter(mock_project)
        writer.log_generated([{"id": "WP-1"}])
        writer.log_added({"id": "WP-2"})

        entries = WaypointHistoryReader.load(mock_project)

        assert len(entries) == 2
        assert isinstance(entries[0], WaypointHistoryEntry)
        assert entries[0].event_type == "generated"
        assert entries[1].event_type == "added"

    def test_load_preserves_order(self, mock_project: MockProject) -> None:
        """Load preserves chronological order."""
        writer = WaypointHistoryWriter(mock_project)
        writer.log_generated([{"id": "WP-1"}])
        writer.log_added({"id": "WP-2"})
        writer.log_deleted("WP-1", {"id": "WP-1"})

        entries = WaypointHistoryReader.load(mock_project)

        assert entries[0].event_type == "generated"
        assert entries[1].event_type == "added"
        assert entries[2].event_type == "deleted"

    def test_get_initial_waypoints(self, mock_project: MockProject) -> None:
        """Get initially generated waypoints."""
        writer = WaypointHistoryWriter(mock_project)

        initial_waypoints = [
            {"id": "WP-1", "title": "First"},
            {"id": "WP-2", "title": "Second"},
        ]
        writer.log_generated(initial_waypoints)
        writer.log_added({"id": "WP-3"})  # Added later, not initial

        result = WaypointHistoryReader.get_initial_waypoints(mock_project)

        assert result == initial_waypoints

    def test_get_initial_waypoints_none(self, mock_project: MockProject) -> None:
        """Returns None when no generated event exists."""
        result = WaypointHistoryReader.get_initial_waypoints(mock_project)
        assert result is None

    def test_get_initial_waypoints_with_other_events(
        self, mock_project: MockProject
    ) -> None:
        """Get initial waypoints even with other events before."""
        writer = WaypointHistoryWriter(mock_project)

        # Technically this shouldn't happen, but test robustness
        writer.log_added({"id": "WP-0"})
        initial = [{"id": "WP-1"}]
        writer.log_generated(initial)

        result = WaypointHistoryReader.get_initial_waypoints(mock_project)

        assert result == initial

    def test_get_reprioritization_count(self, mock_project: MockProject) -> None:
        """Count reprioritization events."""
        writer = WaypointHistoryWriter(mock_project)

        writer.log_generated([{"id": "WP-1"}])
        writer.log_reprioritized(["WP-1", "WP-2"], ["WP-2", "WP-1"], "First reorder")
        writer.log_added({"id": "WP-3"})
        writer.log_reprioritized(
            ["WP-2", "WP-1", "WP-3"], ["WP-3", "WP-2", "WP-1"], "Second reorder"
        )

        count = WaypointHistoryReader.get_reprioritization_count(mock_project)

        assert count == 2

    def test_get_reprioritization_count_zero(self, mock_project: MockProject) -> None:
        """Count returns 0 when no reprioritizations."""
        writer = WaypointHistoryWriter(mock_project)
        writer.log_generated([{"id": "WP-1"}])
        writer.log_added({"id": "WP-2"})

        count = WaypointHistoryReader.get_reprioritization_count(mock_project)

        assert count == 0

    def test_get_reprioritization_count_empty(self, mock_project: MockProject) -> None:
        """Count returns 0 for empty history."""
        count = WaypointHistoryReader.get_reprioritization_count(mock_project)
        assert count == 0


class TestWaypointHistoryRoundtrip:
    """Integration tests for history write/read roundtrip."""

    def test_full_history_roundtrip(self, mock_project: MockProject) -> None:
        """Full history survives roundtrip."""
        writer = WaypointHistoryWriter(mock_project)

        # Simulate typical workflow
        initial = [
            {"id": "WP-1", "title": "Setup", "objective": "Project setup"},
            {"id": "WP-2", "title": "Core", "objective": "Core features"},
        ]
        writer.log_generated(initial)
        sub_wps = [{"id": "WP-1a", "title": "Sub 1"}, {"id": "WP-1b", "title": "Sub 2"}]
        writer.log_broken_down("WP-1", sub_wps)
        writer.log_added({"id": "WP-3", "title": "Testing"}, insert_after="WP-2")
        writer.log_updated(
            "WP-2",
            {"title": "Core"},
            {"title": "Core Features"},
        )
        writer.log_reprioritized(
            ["WP-1", "WP-2", "WP-3"], ["WP-3", "WP-1", "WP-2"], "Testing first"
        )
        writer.log_deleted("WP-3", {"id": "WP-3", "title": "Testing"})

        # Load and verify
        entries = WaypointHistoryReader.load(mock_project)

        assert len(entries) == 6
        event_types = [e.event_type for e in entries]
        assert event_types == [
            "generated",
            "broken_down",
            "added",
            "updated",
            "reprioritized",
            "deleted",
        ]

        # Verify data integrity
        assert entries[0].data["waypoints"] == initial
        assert entries[1].data["parent_id"] == "WP-1"
        assert entries[2].data["insert_after"] == "WP-2"
        assert entries[3].data["before"]["title"] == "Core"


def _read_jsonl_entries(path: Path) -> list[dict[str, Any]]:
    """Helper to read all entries from a JSONL file."""
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))
    return entries
