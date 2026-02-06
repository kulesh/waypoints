"""Execution report for a waypoint run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from waypoints.fly.executor import ExecutionResult


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Structured summary of a waypoint execution attempt."""

    waypoint_id: str
    result: ExecutionResult
    started_at: datetime | None = None
    completed_at: datetime | None = None
    iterations: int | None = None
    total_iterations: int | None = None
    criteria_completed: set[int] = field(default_factory=set)
