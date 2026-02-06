"""Application-layer orchestration helpers for FLY execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from waypoints.fly.executor import ExecutionResult
from waypoints.models import JourneyState
from waypoints.orchestration.coordinator import JourneyCoordinator
from waypoints.orchestration.types import NextAction


@dataclass(frozen=True)
class ExecutionFailurePolicy:
    """UI-agnostic policy for handling non-success execution results."""

    log_method: Literal["error", "write"]
    log_message: str
    mark_waypoint_failed: bool
    journey_state: JourneyState
    execution_state: Literal["paused", "intervention"]
    status_header_error: bool
    notification_message: str | None = None
    notification_severity: Literal["warning", "error", "information"] | None = None


class FlyService:
    """Service for FLY orchestration decisions independent of UI rendering."""

    def __init__(self, coordinator: JourneyCoordinator) -> None:
        self._coordinator = coordinator

    def select_next_waypoint_action(self, include_failed: bool = False) -> NextAction:
        """Select next waypoint and map selection/completion to a UI action."""
        waypoint = self._coordinator.select_next_waypoint(include_failed=include_failed)
        if waypoint is not None:
            return NextAction(action="continue", waypoint=waypoint)

        status = self._coordinator.get_completion_status()
        if status.all_complete:
            return NextAction(action="complete", message="All waypoints complete!")

        if status.blocked > 0:
            return NextAction(
                action="pause",
                message=f"{status.blocked} waypoint(s) blocked by failures",
            )

        pending_total = status.pending + status.in_progress
        if pending_total > 0:
            return NextAction(
                action="pause",
                message=f"{pending_total} waypoint(s) waiting",
            )

        if status.failed > 0:
            return NextAction(
                action="pause",
                message=f"Only failed waypoints remain ({status.failed})",
            )

        return NextAction(action="pause", message="No executable waypoints available")

    def resolve_non_success_result(
        self, result: ExecutionResult | None
    ) -> ExecutionFailurePolicy:
        """Map non-success execution results to a workflow policy."""
        if result == ExecutionResult.INTERVENTION_NEEDED:
            return ExecutionFailurePolicy(
                log_method="error",
                log_message="Human intervention needed",
                mark_waypoint_failed=True,
                journey_state=JourneyState.FLY_INTERVENTION,
                execution_state="intervention",
                status_header_error=True,
                notification_message="Waypoint needs human intervention",
                notification_severity="warning",
            )

        if result == ExecutionResult.MAX_ITERATIONS:
            return ExecutionFailurePolicy(
                log_method="error",
                log_message="Max iterations reached without completion",
                mark_waypoint_failed=True,
                journey_state=JourneyState.FLY_INTERVENTION,
                execution_state="intervention",
                status_header_error=True,
                notification_message="Max iterations reached",
                notification_severity="error",
            )

        if result == ExecutionResult.CANCELLED:
            return ExecutionFailurePolicy(
                log_method="write",
                log_message="Execution cancelled",
                mark_waypoint_failed=False,
                journey_state=JourneyState.FLY_PAUSED,
                execution_state="paused",
                status_header_error=False,
            )

        return ExecutionFailurePolicy(
            log_method="error",
            log_message="Execution failed",
            mark_waypoint_failed=True,
            journey_state=JourneyState.FLY_INTERVENTION,
            execution_state="intervention",
            status_header_error=True,
            notification_message="Waypoint execution failed",
            notification_severity="error",
        )
