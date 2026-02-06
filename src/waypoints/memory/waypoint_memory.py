"""Waypoint-scoped durable memory for cross-waypoint retrieval."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from waypoints.models.waypoint import Waypoint

WAYPOINT_MEMORY_SCHEMA_VERSION = "v1"
WAYPOINT_MEMORY_DIRNAME = "waypoint"
DEFAULT_MEMORY_CONTEXT_CHARS = 2400
MAX_CONTEXT_RECORDS = 4


@dataclass(slots=True, frozen=True)
class WaypointMemoryRecord:
    """Durable summary from executing one waypoint."""

    schema_version: str
    saved_at_utc: str
    waypoint_id: str
    title: str
    objective: str
    dependencies: tuple[str, ...]
    result: str
    iterations_used: int
    max_iterations: int
    protocol_derailments: tuple[str, ...]
    error_summary: str | None
    changed_files: tuple[str, ...]
    approx_tokens_changed: int | None
    validation_commands: tuple[str, ...]
    useful_commands: tuple[str, ...]
    verified_criteria: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize waypoint memory record."""
        return {
            "schema_version": self.schema_version,
            "saved_at_utc": self.saved_at_utc,
            "waypoint_id": self.waypoint_id,
            "title": self.title,
            "objective": self.objective,
            "dependencies": list(self.dependencies),
            "result": self.result,
            "iterations_used": self.iterations_used,
            "max_iterations": self.max_iterations,
            "protocol_derailments": list(self.protocol_derailments),
            "error_summary": self.error_summary,
            "changed_files": list(self.changed_files),
            "approx_tokens_changed": self.approx_tokens_changed,
            "validation_commands": list(self.validation_commands),
            "useful_commands": list(self.useful_commands),
            "verified_criteria": list(self.verified_criteria),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WaypointMemoryRecord":
        """Deserialize waypoint memory record."""
        return cls(
            schema_version=str(
                data.get("schema_version", WAYPOINT_MEMORY_SCHEMA_VERSION)
            ),
            saved_at_utc=str(data.get("saved_at_utc", "")),
            waypoint_id=str(data.get("waypoint_id", "")),
            title=str(data.get("title", "")),
            objective=str(data.get("objective", "")),
            dependencies=tuple(str(item) for item in data.get("dependencies", [])),
            result=str(data.get("result", "")),
            iterations_used=int(data.get("iterations_used", 0)),
            max_iterations=int(data.get("max_iterations", 0)),
            protocol_derailments=tuple(
                str(item) for item in data.get("protocol_derailments", [])
            ),
            error_summary=(
                str(data.get("error_summary")) if data.get("error_summary") else None
            ),
            changed_files=tuple(str(item) for item in data.get("changed_files", [])),
            approx_tokens_changed=(
                int(data["approx_tokens_changed"])
                if data.get("approx_tokens_changed") is not None
                else None
            ),
            validation_commands=tuple(
                str(item) for item in data.get("validation_commands", [])
            ),
            useful_commands=tuple(
                str(item) for item in data.get("useful_commands", [])
            ),
            verified_criteria=tuple(
                int(item) for item in data.get("verified_criteria", [])
            ),
        )


def waypoint_memory_dir(project_root: Path) -> Path:
    """Return waypoint memory directory path."""
    return project_root / ".waypoints" / "memory" / WAYPOINT_MEMORY_DIRNAME


def waypoint_memory_path(project_root: Path, waypoint_id: str) -> Path:
    """Return memory file path for a specific waypoint."""
    slug = waypoint_id.lower().replace(" ", "_")
    return waypoint_memory_dir(project_root) / f"{slug}.json"


def save_waypoint_memory(project_root: Path, record: WaypointMemoryRecord) -> Path:
    """Persist waypoint memory record to disk."""
    destination = waypoint_memory_path(project_root, record.waypoint_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def load_waypoint_memory(
    project_root: Path, waypoint_id: str
) -> WaypointMemoryRecord | None:
    """Load waypoint memory for a specific waypoint."""
    path = waypoint_memory_path(project_root, waypoint_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return WaypointMemoryRecord.from_dict(data)
    except (TypeError, ValueError):
        return None


def build_waypoint_memory_context(
    *,
    project_root: Path,
    waypoint: "Waypoint",
    max_chars: int = DEFAULT_MEMORY_CONTEXT_CHARS,
    max_records: int = MAX_CONTEXT_RECORDS,
) -> str:
    """Build token-bounded memory context for a new waypoint."""
    records = _select_relevant_records(
        project_root=project_root,
        waypoint=waypoint,
        max_records=max_records,
    )
    if not records:
        return ""

    lines = [
        (
            "Use the following prior waypoint memory as guidance "
            "(not as hard constraints):"
        ),
    ]
    for record in records:
        dependency_flag = (
            "dependency" if record.waypoint_id in waypoint.dependencies else "recent"
        )
        lines.append(
            (
                f"- {record.waypoint_id} ({dependency_flag}, result={record.result}, "
                f"iterations={record.iterations_used}/{record.max_iterations})"
            )
        )
        if record.changed_files:
            changed = ", ".join(record.changed_files[:5])
            lines.append(f"  changed_files: {changed}")
        if record.validation_commands:
            commands = "; ".join(record.validation_commands[:4])
            lines.append(f"  validations: {commands}")
        if record.protocol_derailments:
            lines.append(f"  pitfalls: {'; '.join(record.protocol_derailments[:2])}")
        if record.error_summary:
            lines.append(f"  caution: {record.error_summary}")

    content = "\n".join(lines)
    if len(content) <= max_chars:
        return content
    return content[: max_chars - 3].rstrip() + "..."


def _select_relevant_records(
    *,
    project_root: Path,
    waypoint: "Waypoint",
    max_records: int,
) -> list[WaypointMemoryRecord]:
    """Select memory records by dependency first, then lexical relevance and recency."""
    all_records = _load_all_waypoint_memory(project_root)
    if not all_records:
        return []

    dependency_ids = set(waypoint.dependencies)
    selected: list[WaypointMemoryRecord] = []
    used_ids: set[str] = set()

    dependency_records = [
        record for record in all_records if record.waypoint_id in dependency_ids
    ]
    dependency_records.sort(
        key=lambda record: _timestamp_sort_key(record.saved_at_utc),
        reverse=True,
    )
    for record in dependency_records:
        if len(selected) >= max_records:
            return selected
        selected.append(record)
        used_ids.add(record.waypoint_id)

    ranked_remaining = sorted(
        (
            record
            for record in all_records
            if record.waypoint_id != waypoint.id and record.waypoint_id not in used_ids
        ),
        key=lambda record: (
            _lexical_similarity(record, waypoint),
            _timestamp_sort_key(record.saved_at_utc),
        ),
        reverse=True,
    )
    for record in ranked_remaining:
        if len(selected) >= max_records:
            break
        selected.append(record)
    return selected


def _load_all_waypoint_memory(project_root: Path) -> list[WaypointMemoryRecord]:
    """Load all waypoint memory records from disk."""
    root = waypoint_memory_dir(project_root)
    if not root.exists():
        return []

    records: list[WaypointMemoryRecord] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            records.append(WaypointMemoryRecord.from_dict(payload))
        except (TypeError, ValueError):
            continue
    return records


def _lexical_similarity(record: WaypointMemoryRecord, waypoint: "Waypoint") -> int:
    """Simple overlap score between current waypoint and historical memory."""
    current_tokens = _tokenize(f"{waypoint.title} {waypoint.objective}")
    memory_tokens = _tokenize(f"{record.title} {record.objective}")
    if not current_tokens or not memory_tokens:
        return 0
    return len(current_tokens & memory_tokens)


def _tokenize(text: str) -> set[str]:
    """Tokenize text for rough lexical matching."""
    tokens = set(re.findall(r"[a-z0-9_]{3,}", text.lower()))
    stopwords = {
        "the",
        "and",
        "with",
        "for",
        "from",
        "that",
        "this",
        "into",
        "use",
        "waypoint",
        "implement",
    }
    return {token for token in tokens if token not in stopwords}


def _timestamp_sort_key(value: str) -> datetime:
    """Parse ISO timestamp for sorting; invalid values sink to epoch."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
