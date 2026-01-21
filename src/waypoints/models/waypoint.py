"""Waypoint data model for flight plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Self


class WaypointStatus(Enum):
    """Status of a waypoint."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    SKIPPED = "skipped"
    COMPLETE = "complete"


@dataclass(slots=True)
class Waypoint:
    """A single waypoint in the flight plan."""

    id: str
    title: str
    objective: str
    acceptance_criteria: list[str] = field(default_factory=list)
    parent_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    status: WaypointStatus = WaypointStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "acceptance_criteria": self.acceptance_criteria,
            "parent_id": self.parent_id,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            title=data["title"],
            objective=data["objective"],
            acceptance_criteria=data.get("acceptance_criteria", []),
            parent_id=data.get("parent_id"),
            dependencies=data.get("dependencies", []),
            status=WaypointStatus(data.get("status", "pending")),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.now()
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at")
                else None
            ),
        )
