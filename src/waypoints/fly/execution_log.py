"""Execution log persistence for waypoint implementation.

Captures all agent interactions during waypoint execution in JSONL format,
similar to dialogue history but with execution-specific metadata.

Each execution creates a log file: fly-{waypoint_id}-{timestamp}.jsonl
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from waypoints.models.schema import migrate_if_needed, write_schema_fields

if TYPE_CHECKING:
    from waypoints.models.project import Project
    from waypoints.models.waypoint import Waypoint


@dataclass
class ExecutionEntry:
    """A single entry in the execution log."""

    entry_type: str  # "iteration", "tool_call", "output", "result", "error"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    iteration: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "entry_type": self.entry_type,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "iteration": self.iteration,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionEntry":
        """Create from dictionary."""
        return cls(
            entry_type=data["entry_type"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            iteration=data.get("iteration", 0),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ExecutionLog:
    """Complete execution log for a waypoint."""

    waypoint_id: str
    waypoint_title: str
    entries: list[ExecutionEntry] = field(default_factory=list)
    execution_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    result: str | None = None  # "success", "failed", "max_iterations", etc.
    total_cost_usd: float = 0.0

    def add_entry(
        self,
        entry_type: str,
        content: str,
        iteration: int = 0,
        **metadata: Any,
    ) -> ExecutionEntry:
        """Add an entry to the log."""
        entry = ExecutionEntry(
            entry_type=entry_type,
            content=content,
            iteration=iteration,
            metadata=metadata,
        )
        self.entries.append(entry)
        return entry

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "waypoint_id": self.waypoint_id,
            "waypoint_title": self.waypoint_title,
            "entries": [e.to_dict() for e in self.entries],
            "execution_id": self.execution_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "result": self.result,
            "total_cost_usd": self.total_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionLog":
        """Create from dictionary."""
        log = cls(
            waypoint_id=data["waypoint_id"],
            waypoint_title=data["waypoint_title"],
            execution_id=data["execution_id"],
            started_at=datetime.fromisoformat(data["started_at"]),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at")
                else None
            ),
            result=data.get("result"),
            total_cost_usd=data.get("total_cost_usd", 0.0),
        )
        log.entries = [ExecutionEntry.from_dict(e) for e in data.get("entries", [])]
        return log


class ExecutionLogWriter:
    """Streams execution log entries to JSONL as they happen.

    Format:
    - Line 1: Header with waypoint info and execution metadata
    - Subsequent lines: Individual log entries (iterations, outputs, etc.)
    - Final line: Completion record with result and cost
    """

    def __init__(self, project: "Project", waypoint: "Waypoint") -> None:
        """Initialize the log writer.

        Args:
            project: The project this execution belongs to
            waypoint: The waypoint being executed
        """
        self.project = project
        self.waypoint = waypoint
        self.execution_id = str(uuid4())
        self.started_at = datetime.now()
        self.file_path = self._generate_path()
        self.total_cost_usd = 0.0
        self._write_header()

    def _generate_path(self) -> Path:
        """Generate the JSONL file path for this execution."""
        fly_dir = self.project.get_sessions_path() / "fly"
        fly_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        wp_id = self.waypoint.id.lower().replace("-", "")
        return fly_dir / f"{wp_id}-{timestamp}.jsonl"

    def _write_header(self) -> None:
        """Write the execution header as the first line."""
        header = {
            "type": "header",
            **write_schema_fields("execution_log"),
            "execution_id": self.execution_id,
            "waypoint_id": self.waypoint.id,
            "waypoint_title": self.waypoint.title,
            "waypoint_objective": self.waypoint.objective,
            "acceptance_criteria": self.waypoint.acceptance_criteria,
            "started_at": self.started_at.isoformat(),
            "project_slug": self.project.slug,
        }
        with open(self.file_path, "w") as f:
            f.write(json.dumps(header) + "\n")

    def log_iteration_start(self, iteration: int, prompt: str) -> None:
        """Log the start of an iteration with the prompt sent."""
        entry = {
            "type": "iteration_start",
            "iteration": iteration,
            "prompt": prompt,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_output(
        self,
        iteration: int,
        output: str,
        criteria_completed: set[int] | None = None,
    ) -> None:
        """Log agent output/response with optional criteria completion status."""
        entry: dict[str, Any] = {
            "type": "output",
            "iteration": iteration,
            "content": output,
            "timestamp": datetime.now().isoformat(),
        }
        if criteria_completed:
            entry["criteria_completed"] = sorted(criteria_completed)
        self._append(entry)

    def log_tool_call(
        self,
        iteration: int,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str | None = None,
    ) -> None:
        """Log a tool call made by the agent."""
        entry = {
            "type": "tool_call",
            "iteration": iteration,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_iteration_end(
        self,
        iteration: int,
        cost_usd: float | None = None,
    ) -> None:
        """Log the end of an iteration."""
        if cost_usd:
            self.total_cost_usd += cost_usd

        entry = {
            "type": "iteration_end",
            "iteration": iteration,
            "cost_usd": cost_usd,
            "cumulative_cost_usd": self.total_cost_usd,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_error(self, iteration: int, error: str) -> None:
        """Log an error during execution."""
        entry = {
            "type": "error",
            "iteration": iteration,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_completion(self, result: str) -> None:
        """Log execution completion with final result."""
        entry = {
            "type": "completion",
            "result": result,
            "total_cost_usd": self.total_cost_usd,
            "started_at": self.started_at.isoformat(),
            "completed_at": datetime.now().isoformat(),
            "duration_seconds": (datetime.now() - self.started_at).total_seconds(),
        }
        self._append(entry)

    def log_intervention_needed(
        self, iteration: int, intervention_type: str, reason: str
    ) -> None:
        """Log when intervention is needed."""
        entry = {
            "type": "intervention_needed",
            "iteration": iteration,
            "intervention_type": intervention_type,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_intervention_resolved(self, action: str, **params: Any) -> None:
        """Log user's intervention decision."""
        entry: dict[str, Any] = {
            "type": "intervention_resolved",
            "action": action,
            "timestamp": datetime.now().isoformat(),
        }
        if params:
            entry["params"] = params
        self._append(entry)

    def log_state_transition(
        self, from_state: str, to_state: str, reason: str = ""
    ) -> None:
        """Log execution state changes."""
        entry: dict[str, Any] = {
            "type": "state_transition",
            "from_state": from_state,
            "to_state": to_state,
            "timestamp": datetime.now().isoformat(),
        }
        if reason:
            entry["reason"] = reason
        self._append(entry)

    def log_receipt_validated(self, path: str, valid: bool, message: str = "") -> None:
        """Log receipt validation result."""
        entry: dict[str, Any] = {
            "type": "receipt_validated",
            "path": path,
            "valid": valid,
            "timestamp": datetime.now().isoformat(),
        }
        if message:
            entry["message"] = message
        self._append(entry)

    def log_git_commit(
        self, success: bool, commit_hash: str = "", message: str = ""
    ) -> None:
        """Log git commit attempt."""
        entry: dict[str, Any] = {
            "type": "git_commit",
            "success": success,
            "timestamp": datetime.now().isoformat(),
        }
        if commit_hash:
            entry["commit_hash"] = commit_hash
        if message:
            entry["message"] = message
        self._append(entry)

    def log_pause(self) -> None:
        """Log execution paused."""
        entry = {
            "type": "pause",
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_resume(self) -> None:
        """Log execution resumed."""
        entry = {
            "type": "resume",
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_security_violation(self, iteration: int, details: str) -> None:
        """Log security violation detected."""
        entry = {
            "type": "security_violation",
            "iteration": iteration,
            "details": details,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_completion_detected(self, iteration: int) -> None:
        """Log when completion marker found."""
        entry = {
            "type": "completion_detected",
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_finalize_start(self) -> None:
        """Log the start of the finalize phase (receipt verification)."""
        entry = {
            "type": "finalize_start",
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_finalize_end(self, cost_usd: float | None = None) -> None:
        """Log the end of the finalize phase."""
        if cost_usd:
            self.total_cost_usd += cost_usd

        entry = {
            "type": "finalize_end",
            "cost_usd": cost_usd,
            "cumulative_cost_usd": self.total_cost_usd,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_finalize_output(self, output: str) -> None:
        """Log finalize phase output."""
        entry = {
            "type": "finalize_output",
            "content": output,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def log_finalize_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: str | None = None,
    ) -> None:
        """Log a tool call during finalize phase."""
        entry = {
            "type": "finalize_tool_call",
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_output": tool_output,
            "timestamp": datetime.now().isoformat(),
        }
        self._append(entry)

    def _append(self, entry: dict[str, Any]) -> None:
        """Append an entry to the JSONL file."""
        with open(self.file_path, "a") as f:
            f.write(json.dumps(entry) + "\n")


class ExecutionLogReader:
    """Reads execution logs from JSONL files."""

    @classmethod
    def load(cls, file_path: Path) -> ExecutionLog:
        """Load an execution log from a JSONL file.

        Automatically migrates legacy files to current schema version.
        """
        # Migrate legacy files if needed
        migrate_if_needed(file_path, "execution_log")

        log: ExecutionLog | None = None
        entries: list[ExecutionEntry] = []

        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)
                entry_type = data.get("type", "")

                if entry_type == "header":
                    log = ExecutionLog(
                        waypoint_id=data["waypoint_id"],
                        waypoint_title=data["waypoint_title"],
                        execution_id=data["execution_id"],
                        started_at=datetime.fromisoformat(data["started_at"]),
                    )
                elif entry_type == "completion":
                    if log:
                        log.result = data.get("result")
                        log.total_cost_usd = data.get("total_cost_usd", 0.0)
                        if data.get("completed_at"):
                            log.completed_at = datetime.fromisoformat(
                                data["completed_at"]
                            )
                else:
                    # Convert to ExecutionEntry
                    content = data.get("content") or data.get("prompt") or ""
                    if entry_type == "error":
                        content = data.get("error", "")
                    elif entry_type == "tool_call":
                        content = f"{data.get('tool_name')}: {data.get('tool_input')}"

                    entries.append(
                        ExecutionEntry(
                            entry_type=entry_type,
                            content=content,
                            timestamp=datetime.fromisoformat(data["timestamp"]),
                            iteration=data.get("iteration", 0),
                            metadata=data,
                        )
                    )

        if not log:
            raise ValueError(f"Invalid execution log file: {file_path}")

        log.entries = entries
        return log

    @classmethod
    def list_logs(
        cls,
        project: "Project",
        waypoint_id: str | None = None,
    ) -> list[Path]:
        """List execution log files for a project.

        Args:
            project: The project to list logs for
            waypoint_id: Optional waypoint ID filter

        Returns:
            List of log file paths, sorted by modification time (newest first)
        """
        fly_dir = project.get_sessions_path() / "fly"
        if not fly_dir.exists():
            return []

        if waypoint_id:
            wp_prefix = waypoint_id.lower().replace("-", "")
            pattern = f"{wp_prefix}-*.jsonl"
        else:
            pattern = "*.jsonl"

        files = list(fly_dir.glob(pattern))
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    @classmethod
    def load_latest(
        cls,
        project: "Project",
        waypoint_id: str | None = None,
    ) -> ExecutionLog | None:
        """Load the most recent execution log.

        Args:
            project: The project to load from
            waypoint_id: Optional waypoint ID filter

        Returns:
            ExecutionLog if found, None otherwise
        """
        logs = cls.list_logs(project, waypoint_id)
        if not logs:
            return None
        return cls.load(logs[0])

    @classmethod
    def get_completed_criteria(
        cls,
        project: "Project",
        waypoint_id: str,
    ) -> set[int]:
        """Get completed criteria indices from the most recent execution log.

        Args:
            project: The project to load from
            waypoint_id: The waypoint ID to get criteria for

        Returns:
            Set of completed criteria indices
        """
        logs = cls.list_logs(project, waypoint_id)
        if not logs:
            return set()

        completed: set[int] = set()
        with open(logs[0]) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("type") == "output" and "criteria_completed" in data:
                    completed.update(data["criteria_completed"])
        return completed
