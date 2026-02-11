"""Shared execution types for fly waypoint execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from waypoints.fly.evidence import FileOperation
from waypoints.models.waypoint import Waypoint

if TYPE_CHECKING:
    from waypoints.fly.provenance import WorkspaceSnapshot
    from waypoints.git.receipt import CapturedEvidence, CriterionVerification


class ExecutionResult(Enum):
    """Result of waypoint execution."""

    SUCCESS = "success"
    FAILED = "failed"
    MAX_ITERATIONS = "max_iterations"
    CANCELLED = "cancelled"
    INTERVENTION_NEEDED = "intervention_needed"


@dataclass
class ExecutionStep:
    """A single step in waypoint execution."""

    iteration: int
    action: str
    output: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ExecutionContext:
    """Context passed to callbacks during execution."""

    waypoint: Waypoint
    iteration: int
    total_iterations: int
    step: str
    output: str
    criteria_completed: set[int] = field(default_factory=set)
    file_operations: list[FileOperation] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionMetricsUpdate:
    """Structured metrics update emitted during waypoint execution."""

    role: str
    waypoint_id: str
    delta_cost_usd: float | None = None
    delta_tokens_in: int | None = None
    delta_tokens_out: int | None = None
    delta_cached_tokens_in: int | None = None
    waypoint_cost_usd: float | None = None
    waypoint_tokens_in: int | None = None
    waypoint_tokens_out: int | None = None
    waypoint_cached_tokens_in: int | None = None
    project_cost_usd: float | None = None
    project_tokens_in: int | None = None
    project_tokens_out: int | None = None
    project_cached_tokens_in: int | None = None
    tokens_known: bool = False
    cached_tokens_known: bool = False

    def to_metadata(self) -> dict[str, object]:
        """Convert to metadata payload for ExecutionContext transport."""
        payload: dict[str, object] = {
            "role": self.role,
            "waypoint_id": self.waypoint_id,
            "tokens_known": self.tokens_known,
            "cached_tokens_known": self.cached_tokens_known,
        }
        optional_fields = (
            ("delta_cost_usd", self.delta_cost_usd),
            ("delta_tokens_in", self.delta_tokens_in),
            ("delta_tokens_out", self.delta_tokens_out),
            ("delta_cached_tokens_in", self.delta_cached_tokens_in),
            ("waypoint_cost_usd", self.waypoint_cost_usd),
            ("waypoint_tokens_in", self.waypoint_tokens_in),
            ("waypoint_tokens_out", self.waypoint_tokens_out),
            ("waypoint_cached_tokens_in", self.waypoint_cached_tokens_in),
            ("project_cost_usd", self.project_cost_usd),
            ("project_tokens_in", self.project_tokens_in),
            ("project_tokens_out", self.project_tokens_out),
            ("project_cached_tokens_in", self.project_cached_tokens_in),
        )
        for key, value in optional_fields:
            if value is not None:
                payload[key] = value
        return payload


ProgressCallback = Callable[[ExecutionContext], None]


@dataclass
class _LoopState:
    """Mutable state accumulated across iterations of the execution loop."""

    iteration: int = 0
    full_output: str = ""
    reported_validation_commands: list[str] = field(default_factory=list)
    captured_criteria: dict[int, "CriterionVerification"] = field(default_factory=dict)
    tool_validation_evidence: dict[str, "CapturedEvidence"] = field(
        default_factory=dict
    )
    tool_validation_categories: dict[str, "CapturedEvidence"] = field(
        default_factory=dict
    )
    logged_stage_reports: set[tuple[object, ...]] = field(default_factory=set)
    completion_detected: bool = False
    completion_iteration: int | None = None
    completion_output: str | None = None
    completion_criteria: set[int] | None = None
    resume_session_id: str | None = None
    next_reason_code: str = "initial"
    next_reason_detail: str = "Initial waypoint execution."
    protocol_derailment_streak: int = 0
    protocol_derailments: list[str] = field(default_factory=list)
    clarification_rounds: int = 0
    clarification_signatures: set[str] = field(default_factory=set)
    unresolved_clarification: bool = False
    clarification_exhausted: bool = False
    waypoint_cost_usd: float = 0.0
    waypoint_tokens_in: int = 0
    waypoint_tokens_out: int = 0
    waypoint_cached_tokens_in: int = 0
    waypoint_tokens_known: bool = False
    waypoint_cached_tokens_known: bool = False
    workspace_before: "WorkspaceSnapshot | None" = None
    prompt: str = ""
    completion_marker: str = ""
    # Per-iteration state (reset at start of each _run_iteration)
    iter_scope_drift_detected: bool = False
    iter_stage_reports_logged: int = 0
    iteration_tokens_in: int | None = None
    iteration_tokens_out: int | None = None
    iteration_cached_tokens_in: int | None = None
    last_tool_name: str | None = None
    last_tool_input: dict[str, object] = field(default_factory=dict)
    last_tool_output: str | None = None
