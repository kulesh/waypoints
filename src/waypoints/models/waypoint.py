"""Waypoint data model for flight plan."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class WaypointStatus(Enum):
    """Status of a waypoint."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    SKIPPED = "skipped"
    COMPLETE = "complete"


@dataclass
class Waypoint:
    """A single waypoint in the flight plan."""

    id: str
    title: str
    objective: str
    acceptance_criteria: list[str] = field(default_factory=list)
    parent_id: str | None = None
    debug_of: str | None = None
    resolution_notes: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    spec_context_summary: str = ""
    spec_section_refs: list[str] = field(default_factory=list)
    spec_context_hash: str | None = None
    status: WaypointStatus = WaypointStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "acceptance_criteria": self.acceptance_criteria,
            "parent_id": self.parent_id,
            "debug_of": self.debug_of,
            "resolution_notes": self.resolution_notes,
            "dependencies": self.dependencies,
            "spec_context_summary": self.spec_context_summary,
            "spec_section_refs": self.spec_section_refs,
            "spec_context_hash": self.spec_context_hash,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Waypoint":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            title=data["title"],
            objective=data["objective"],
            acceptance_criteria=data.get("acceptance_criteria", []),
            parent_id=data.get("parent_id"),
            debug_of=data.get("debug_of"),
            resolution_notes=data.get("resolution_notes", []),
            dependencies=data.get("dependencies", []),
            spec_context_summary=data.get("spec_context_summary", ""),
            spec_section_refs=data.get("spec_section_refs", []),
            spec_context_hash=data.get("spec_context_hash"),
            status=WaypointStatus(data.get("status", "pending")),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.now(UTC)
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at")
                else None
            ),
        )
