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
    workspace_before: "WorkspaceSnapshot | None" = None
    prompt: str = ""
    completion_marker: str = ""
    # Per-iteration state (reset at start of each _run_iteration)
    iter_scope_drift_detected: bool = False
    iter_stage_reports_logged: int = 0
    last_tool_name: str | None = None
    last_tool_input: dict[str, object] = field(default_factory=dict)
    last_tool_output: str | None = None
