"""FlightPlan model for managing waypoints."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterator

from waypoints.models.schema import migrate_if_needed, write_schema_fields
from waypoints.models.waypoint import Waypoint

if TYPE_CHECKING:
    from waypoints.models.project import Project


@dataclass
class FlightPlan:
    """Container for all waypoints in a project."""

    waypoints: list[Waypoint] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def get_waypoint(self, waypoint_id: str) -> Waypoint | None:
        """Get a waypoint by ID."""
        for wp in self.waypoints:
            if wp.id == waypoint_id:
                return wp
        return None

    def get_children(self, parent_id: str) -> list[Waypoint]:
        """Get all direct children of a waypoint."""
        return [wp for wp in self.waypoints if wp.parent_id == parent_id]

    def get_root_waypoints(self) -> list[Waypoint]:
        """Get top-level waypoints (no parent)."""
        return [wp for wp in self.waypoints if wp.parent_id is None]

    def is_epic(self, waypoint_id: str) -> bool:
        """Check if a waypoint has children (is a multi-hop waypoint)."""
        return any(wp.parent_id == waypoint_id for wp in self.waypoints)

    def get_dependents(self, waypoint_id: str) -> list[Waypoint]:
        """Get waypoints that depend on this one."""
        return [wp for wp in self.waypoints if waypoint_id in wp.dependencies]

    def add_waypoint(self, waypoint: Waypoint) -> None:
        """Add a waypoint to the plan."""
        self.waypoints.append(waypoint)
        self.updated_at = datetime.now()

    def insert_waypoints_after(self, parent_id: str, waypoints: list[Waypoint]) -> None:
        """Insert waypoints immediately after a parent waypoint.

        This maintains file order so children are processed before
        unrelated waypoints that appear later in the file.

        Args:
            parent_id: The ID of the parent waypoint to insert after.
            waypoints: List of waypoints to insert.
        """
        parent_idx = next(
            (i for i, wp in enumerate(self.waypoints) if wp.id == parent_id),
            None,
        )
        if parent_idx is None:
            # Parent not found, append at end
            self.waypoints.extend(waypoints)
        else:
            # Insert after parent
            for i, wp in enumerate(waypoints):
                self.waypoints.insert(parent_idx + 1 + i, wp)
        self.updated_at = datetime.now()

    def insert_waypoint_at(self, waypoint: Waypoint, after_id: str | None) -> None:
        """Insert a single waypoint at a specific position.

        Args:
            waypoint: The waypoint to insert.
            after_id: Insert after this waypoint ID. If None, insert at the beginning.
        """
        if after_id is None:
            self.waypoints.insert(0, waypoint)
        else:
            idx = next(
                (i for i, wp in enumerate(self.waypoints) if wp.id == after_id),
                None,
            )
            if idx is None:
                # Fallback: append to end
                self.waypoints.append(waypoint)
            else:
                self.waypoints.insert(idx + 1, waypoint)
        self.updated_at = datetime.now()

    def update_waypoint(self, waypoint: Waypoint) -> bool:
        """Update an existing waypoint.

        Args:
            waypoint: The waypoint with updated fields.

        Returns:
            True if waypoint was found and updated, False otherwise.
        """
        for i, wp in enumerate(self.waypoints):
            if wp.id == waypoint.id:
                self.waypoints[i] = waypoint
                self.updated_at = datetime.now()
                return True
        return False

    def remove_waypoint(self, waypoint_id: str) -> None:
        """Remove a waypoint, its children, and update dependencies.

        This recursively removes all child waypoints to prevent orphaned
        waypoints with dangling parent_id references.
        """
        # First, recursively remove all children
        children = self.get_children(waypoint_id)
        for child in children:
            self.remove_waypoint(child.id)

        # Remove the waypoint itself
        self.waypoints = [wp for wp in self.waypoints if wp.id != waypoint_id]
        # Update any waypoints that depended on this one
        for wp in self.waypoints:
            wp.dependencies = [d for d in wp.dependencies if d != waypoint_id]
        self.updated_at = datetime.now()

    def iterate_in_order(self) -> Iterator[tuple[Waypoint, int]]:
        """Iterate waypoints in display order with depth level.

        Yields:
            Tuple of (waypoint, depth) for each waypoint in tree order.
        """

        def _iterate(
            parent_id: str | None, depth: int
        ) -> Iterator[tuple[Waypoint, int]]:
            children = [wp for wp in self.waypoints if wp.parent_id == parent_id]
            for child in children:
                yield (child, depth)
                yield from _iterate(child.id, depth + 1)

        yield from _iterate(None, 0)

    def validate_dependencies(self) -> list[str]:
        """Check for circular dependencies.

        Returns:
            List of error messages (empty if valid).
        """
        errors: list[str] = []
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def has_cycle(wp_id: str) -> bool:
            visited.add(wp_id)
            rec_stack.add(wp_id)

            wp = self.get_waypoint(wp_id)
            if wp:
                for dep_id in wp.dependencies:
                    if dep_id not in visited:
                        if has_cycle(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True

            rec_stack.remove(wp_id)
            return False

        for wp in self.waypoints:
            if wp.id not in visited:
                if has_cycle(wp.id):
                    errors.append(f"Circular dependency detected involving {wp.id}")

        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "waypoints": [wp.to_dict() for wp in self.waypoints],
        }


class FlightPlanWriter:
    """Persists flight plan to JSONL."""

    def __init__(self, project: "Project") -> None:
        """Initialize writer for a project."""
        self.project = project
        self.file_path = project.get_path() / "flight-plan.jsonl"

    def save(self, flight_plan: FlightPlan) -> None:
        """Save entire flight plan (overwrites file)."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w") as f:
            # Header line with schema version
            header = {
                **write_schema_fields("flight_plan"),
                "created_at": flight_plan.created_at.isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            f.write(json.dumps(header) + "\n")
            # Waypoint lines
            for wp in flight_plan.waypoints:
                f.write(json.dumps(wp.to_dict()) + "\n")

    def append_waypoint(self, waypoint: Waypoint) -> None:
        """Append a single waypoint (for streaming generation)."""
        with open(self.file_path, "a") as f:
            f.write(json.dumps(waypoint.to_dict()) + "\n")


class FlightPlanReader:
    """Reads flight plan from JSONL."""

    @classmethod
    def load(cls, project: "Project") -> FlightPlan | None:
        """Load flight plan from project.

        Automatically migrates legacy files to current schema version.

        Args:
            project: The project to load from

        Returns:
            FlightPlan if file exists, None otherwise.
        """
        file_path = project.get_path() / "flight-plan.jsonl"
        if not file_path.exists():
            return None

        # Migrate legacy files if needed
        migrate_if_needed(file_path, "flight_plan")

        flight_plan = FlightPlan()

        with open(file_path) as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)

                if line_num == 0 and "created_at" in data and "id" not in data:
                    # Header line (may include _schema, _version which we ignore)
                    flight_plan.created_at = datetime.fromisoformat(data["created_at"])
                    if "updated_at" in data:
                        flight_plan.updated_at = datetime.fromisoformat(
                            data["updated_at"]
                        )
                else:
                    # Waypoint line
                    flight_plan.waypoints.append(Waypoint.from_dict(data))

        return flight_plan

    @classmethod
    def exists(cls, project: "Project") -> bool:
        """Check if a flight plan exists for the project."""
        return (project.get_path() / "flight-plan.jsonl").exists()
