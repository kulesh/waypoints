"""Waypoint history tracking for audit/provenance.

Captures all waypoint changes (add, delete, update, breakdown, reprioritize)
in JSONL format for debugging and potential future undo/history features.

Storage: sessions/chart/waypoint_history.jsonl
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waypoints.models.project import Project


@dataclass
class WaypointHistoryEntry:
    """A single entry in the waypoint history log."""

    # Types: "generated", "added", "deleted", "updated", "broken_down", "reprioritized"
    event_type: str
    timestamp: datetime
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WaypointHistoryEntry":
        """Create from dictionary."""
        return cls(
            event_type=data["event_type"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            data=data.get("data", {}),
        )


class WaypointHistoryWriter:
    """Appends waypoint change events to JSONL log."""

    def __init__(self, project: "Project") -> None:
        """Initialize the history writer.

        Args:
            project: The project this history belongs to
        """
        self.project = project
        self.file_path = self._get_path()

    def _get_path(self) -> Path:
        """Get the JSONL file path for waypoint history."""
        chart_dir = self.project.get_sessions_path() / "chart"
        chart_dir.mkdir(parents=True, exist_ok=True)
        return chart_dir / "waypoint_history.jsonl"

    def _append(self, entry: dict[str, Any]) -> None:
        """Append an entry to the JSONL file."""
        with open(self.file_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def log_generated(self, waypoints: list[dict[str, Any]]) -> None:
        """Log initial waypoint generation.

        Args:
            waypoints: List of waypoint dictionaries (id, title, objective, etc.)
        """
        entry = WaypointHistoryEntry(
            event_type="generated",
            timestamp=datetime.now(),
            data={"waypoints": waypoints},
        )
        self._append(entry.to_dict())

    def log_added(
        self,
        waypoint: dict[str, Any],
        insert_after: str | None = None,
    ) -> None:
        """Log a waypoint being added.

        Args:
            waypoint: The waypoint data that was added
            insert_after: ID of waypoint to insert after, or None for append
        """
        entry = WaypointHistoryEntry(
            event_type="added",
            timestamp=datetime.now(),
            data={
                "waypoint": waypoint,
                "insert_after": insert_after,
            },
        )
        self._append(entry.to_dict())

    def log_deleted(self, waypoint_id: str, waypoint_data: dict[str, Any]) -> None:
        """Log a waypoint being deleted.

        Args:
            waypoint_id: ID of the deleted waypoint
            waypoint_data: Full waypoint data before deletion
        """
        entry = WaypointHistoryEntry(
            event_type="deleted",
            timestamp=datetime.now(),
            data={
                "waypoint_id": waypoint_id,
                "waypoint": waypoint_data,
            },
        )
        self._append(entry.to_dict())

    def log_updated(
        self,
        waypoint_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        """Log a waypoint being updated.

        Args:
            waypoint_id: ID of the updated waypoint
            before: Waypoint data before update
            after: Waypoint data after update
        """
        entry = WaypointHistoryEntry(
            event_type="updated",
            timestamp=datetime.now(),
            data={
                "waypoint_id": waypoint_id,
                "before": before,
                "after": after,
            },
        )
        self._append(entry.to_dict())

    def log_broken_down(
        self,
        parent_id: str,
        sub_waypoints: list[dict[str, Any]],
    ) -> None:
        """Log waypoint breakdown into sub-waypoints.

        Args:
            parent_id: ID of the parent waypoint
            sub_waypoints: List of sub-waypoint dictionaries
        """
        entry = WaypointHistoryEntry(
            event_type="broken_down",
            timestamp=datetime.now(),
            data={
                "parent_id": parent_id,
                "sub_waypoints": sub_waypoints,
            },
        )
        self._append(entry.to_dict())

    def log_reprioritized(
        self,
        previous_order: list[str],
        new_order: list[str],
        rationale: str,
        changes: list[dict[str, str]] | None = None,
    ) -> None:
        """Log waypoint reprioritization.

        Args:
            previous_order: List of waypoint IDs in previous order
            new_order: List of waypoint IDs in new order
            rationale: AI's explanation for the new order
            changes: Optional list of per-waypoint change reasons
        """
        entry = WaypointHistoryEntry(
            event_type="reprioritized",
            timestamp=datetime.now(),
            data={
                "previous_order": previous_order,
                "new_order": new_order,
                "rationale": rationale,
                "changes": changes or [],
            },
        )
        self._append(entry.to_dict())


class WaypointHistoryReader:
    """Reads waypoint history from JSONL files."""

    @classmethod
    def load(cls, project: "Project") -> list[WaypointHistoryEntry]:
        """Load all waypoint history entries for a project.

        Args:
            project: The project to load history for

        Returns:
            List of history entries in chronological order
        """
        file_path = project.get_sessions_path() / "chart" / "waypoint_history.jsonl"
        if not file_path.exists():
            return []

        entries: list[WaypointHistoryEntry] = []
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entries.append(WaypointHistoryEntry.from_dict(data))

        return entries

    @classmethod
    def get_initial_waypoints(
        cls, project: "Project"
    ) -> list[dict[str, Any]] | None:
        """Get the initially generated waypoints.

        Args:
            project: The project to load from

        Returns:
            List of initial waypoint dictionaries, or None if not found
        """
        entries = cls.load(project)
        for entry in entries:
            if entry.event_type == "generated":
                return entry.data.get("waypoints")
        return None

    @classmethod
    def get_reprioritization_count(cls, project: "Project") -> int:
        """Count how many times waypoints have been reprioritized.

        Args:
            project: The project to count for

        Returns:
            Number of reprioritization events
        """
        entries = cls.load(project)
        return sum(1 for e in entries if e.event_type == "reprioritized")
