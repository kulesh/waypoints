"""Execution controller for FLY phase orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from waypoints.fly.execution_report import ExecutionReport
from waypoints.fly.executor import (
    ExecutionContext,
    ExecutionResult,
    WaypointExecutor,
)
from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionResult,
)
from waypoints.fly.state import ExecutionState
from waypoints.models import JourneyState, Waypoint, WaypointStatus
from waypoints.orchestration.coordinator import JourneyCoordinator

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutionDirective:
    """Directive returned by the controller after an execution event."""

    action: Literal["execute", "pause", "intervention", "land", "noop"]
    waypoint: Waypoint | None = None
    message: str | None = None
    completed: Waypoint | None = None
    reload_flight_plan: bool = False


class ExecutionController:
    """Orchestrates execution logic for the FLY phase."""

    def __init__(self, coordinator: JourneyCoordinator) -> None:
        self.coordinator = coordinator
        self.execution_state = ExecutionState.IDLE
        self._current_intervention: Intervention | None = None
        self._additional_iterations = 0
        self._execution_started_at: datetime | None = None
        self.last_report: ExecutionReport | None = None

    @property
    def current_waypoint(self) -> Waypoint | None:
        """Get the current waypoint from the coordinator."""
        return self.coordinator.current_waypoint

    @current_waypoint.setter
    def current_waypoint(self, waypoint: Waypoint | None) -> None:
        """Set the current waypoint on the coordinator."""
        self.coordinator.current_waypoint = waypoint

    def initialize(self) -> None:
        """Initialize execution state on screen mount."""
        self.coordinator.reset_stale_in_progress()
        self.select_next_waypoint(include_in_progress=True)

    def select_next_waypoint(
        self, include_in_progress: bool = False
    ) -> Waypoint | None:
        """Select the next eligible waypoint and update execution state."""
        wp = self.coordinator.select_next_waypoint(include_failed=include_in_progress)
        if wp:
            return wp

        status = self.coordinator.get_completion_status()
        pending = status.pending + status.in_progress

        if status.all_complete:
            self.execution_state = ExecutionState.DONE
        elif status.blocked > 0 or pending > 0 or status.failed > 0:
            self.execution_state = ExecutionState.PAUSED
        else:
            self.execution_state = ExecutionState.PAUSED

        return None

    def start(self, selected_waypoint: Waypoint | None) -> ExecutionDirective:
        """Start or resume execution."""
        if selected_waypoint and selected_waypoint.status == WaypointStatus.FAILED:
            selected_waypoint.status = WaypointStatus.PENDING
            self.coordinator.save_flight_plan()
            self.current_waypoint = selected_waypoint
            self._transition_to_executing()
            self.execution_state = ExecutionState.RUNNING
            return ExecutionDirective(
                action="execute",
                waypoint=selected_waypoint,
                message=f"Retrying {selected_waypoint.id}",
            )

        if self.execution_state == ExecutionState.DONE:
            status = self.coordinator.get_completion_status()
            if status.failed > 0 or status.blocked > 0:
                return ExecutionDirective(
                    action="noop",
                    message="Select a failed waypoint and press 'r' to retry",
                )
            return ExecutionDirective(action="noop", message="All waypoints complete!")

        if self.execution_state == ExecutionState.PAUSED:
            self.select_next_waypoint(include_in_progress=True)
            if not self.current_waypoint:
                status = self.coordinator.get_completion_status()
                if status.failed > 0:
                    return ExecutionDirective(
                        action="noop",
                        message="Select a failed waypoint and press 'r' to retry",
                    )
                return ExecutionDirective(
                    action="noop", message="No waypoints to resume"
                )
            self._transition_to_executing()
            self.execution_state = ExecutionState.RUNNING
            return ExecutionDirective(action="execute", waypoint=self.current_waypoint)

        if not self.current_waypoint:
            self.select_next_waypoint()
            if not self.current_waypoint:
                return ExecutionDirective(
                    action="noop", message="No waypoints ready to execute"
                )

        self._transition_to_executing()
        self.execution_state = ExecutionState.RUNNING
        return ExecutionDirective(action="execute", waypoint=self.current_waypoint)

    def request_pause(self) -> bool:
        """Request pause after current waypoint."""
        if self.execution_state != ExecutionState.RUNNING:
            return False
        self.execution_state = ExecutionState.PAUSE_PENDING
        return True

    def build_executor(
        self,
        *,
        waypoint: Waypoint,
        spec: str,
        on_progress: Callable[[ExecutionContext], None] | None,
        max_iterations: int,
        metrics_collector: "MetricsCollector | None",
        host_validations_enabled: bool,
    ) -> WaypointExecutor:
        """Create a WaypointExecutor and mark waypoint as in progress."""
        waypoint.status = WaypointStatus.IN_PROGRESS
        self.coordinator.save_flight_plan()
        self._execution_started_at = datetime.now(UTC)

        total_iterations = max_iterations + self.consume_additional_iterations()
        return WaypointExecutor(
            project=self.coordinator.project,
            waypoint=waypoint,
            spec=spec,
            on_progress=on_progress,
            max_iterations=total_iterations,
            metrics_collector=metrics_collector,
            host_validations_enabled=host_validations_enabled,
        )

    def handle_execution_result(
        self, result: ExecutionResult | None
    ) -> ExecutionDirective:
        """Handle execution result and update state."""
        waypoint = self.current_waypoint
        completed_at = datetime.now(UTC)
        normalized = result or ExecutionResult.FAILED

        if waypoint:
            self.last_report = ExecutionReport(
                waypoint_id=waypoint.id,
                result=normalized,
                started_at=self._execution_started_at,
                completed_at=completed_at,
            )

        if normalized == ExecutionResult.SUCCESS:
            if waypoint:
                waypoint.status = WaypointStatus.COMPLETE
                waypoint.completed_at = completed_at
                self.coordinator.save_flight_plan()
                self.coordinator.check_parent_completion(waypoint)

            if self.execution_state == ExecutionState.PAUSE_PENDING:
                self.coordinator.transition(JourneyState.FLY_PAUSED)
                self.execution_state = ExecutionState.PAUSED
                return ExecutionDirective(
                    action="pause", waypoint=waypoint, completed=waypoint
                )

            if self.execution_state == ExecutionState.RUNNING:
                next_wp = self.select_next_waypoint()
                if next_wp:
                    return ExecutionDirective(
                        action="execute",
                        waypoint=next_wp,
                        completed=waypoint,
                    )
                status = self.coordinator.get_completion_status()
                if status.all_complete:
                    self.coordinator.transition(JourneyState.LAND_REVIEW)
                    return ExecutionDirective(action="land", completed=waypoint)
                return ExecutionDirective(action="pause", completed=waypoint)

            return ExecutionDirective(action="noop", completed=waypoint)

        if normalized == ExecutionResult.CANCELLED:
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            return ExecutionDirective(action="pause", waypoint=waypoint)

        if normalized in (
            ExecutionResult.INTERVENTION_NEEDED,
            ExecutionResult.MAX_ITERATIONS,
            ExecutionResult.FAILED,
        ):
            self._mark_waypoint_failed()
            self.coordinator.transition(JourneyState.FLY_INTERVENTION)
            self.execution_state = ExecutionState.INTERVENTION
            message = self._result_message(normalized)
            return ExecutionDirective(
                action="intervention", waypoint=waypoint, message=message
            )

        self._mark_waypoint_failed()
        self.coordinator.transition(JourneyState.FLY_INTERVENTION)
        self.execution_state = ExecutionState.INTERVENTION
        return ExecutionDirective(
            action="intervention",
            waypoint=waypoint,
            message="Waypoint execution failed",
        )

    def request_land(self) -> ExecutionDirective:
        """Request transition to LAND, returning a directive."""
        journey = self.coordinator.project.journey
        if journey and journey.state == JourneyState.LAND_REVIEW:
            return ExecutionDirective(action="land")

        status = self.coordinator.get_completion_status()
        if status.all_complete:
            self.coordinator.transition(JourneyState.LAND_REVIEW)
            return ExecutionDirective(action="land")

        if self.execution_state == ExecutionState.DONE:
            return ExecutionDirective(
                action="pause",
                message="Cannot land yet - some waypoints are blocked or failed",
            )

        return ExecutionDirective(
            action="pause",
            message="Cannot land yet - waypoints still in progress",
        )

    def prepare_intervention(self, intervention: Intervention) -> ExecutionDirective:
        """Record an intervention and transition state."""
        self._current_intervention = intervention
        self._mark_waypoint_failed()
        self.coordinator.transition(JourneyState.FLY_INTERVENTION)
        self.execution_state = ExecutionState.INTERVENTION
        return ExecutionDirective(
            action="intervention",
            waypoint=intervention.waypoint,
            message=intervention.error_summary,
        )

    def resolve_intervention(
        self, result: InterventionResult | None
    ) -> ExecutionDirective:
        """Resolve an intervention and return next directive."""
        if result is None:
            return ExecutionDirective(action="noop", message="Intervention cancelled")

        if not self._current_intervention:
            return ExecutionDirective(
                action="noop", message="No intervention to resolve"
            )

        waypoint = self._current_intervention.waypoint

        if result.action == InterventionAction.RETRY:
            self._additional_iterations = result.additional_iterations
            waypoint.status = WaypointStatus.IN_PROGRESS
            self.coordinator.save_flight_plan()
            self.coordinator.transition(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            self._current_intervention = None
            return ExecutionDirective(action="execute", waypoint=waypoint)

        if result.action == InterventionAction.SKIP:
            waypoint.status = WaypointStatus.SKIPPED
            self.coordinator.save_flight_plan()
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.coordinator.transition(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            next_wp = self.select_next_waypoint()
            self._current_intervention = None
            if next_wp:
                return ExecutionDirective(action="execute", waypoint=next_wp)
            if self.execution_state == ExecutionState.DONE:
                self.coordinator.transition(JourneyState.LAND_REVIEW)
                return ExecutionDirective(action="land")
            return ExecutionDirective(action="pause")

        if result.action == InterventionAction.EDIT:
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self._current_intervention = None
            return ExecutionDirective(
                action="pause",
                message="Edit waypoint in flight plan, then retry",
            )

        if result.action == InterventionAction.ROLLBACK:
            outcome = self.coordinator.rollback_to_tag(result.rollback_tag)
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self._current_intervention = None

            if outcome.status == "success":
                self.coordinator.transition(JourneyState.FLY_READY)
                self.execution_state = ExecutionState.IDLE
                return ExecutionDirective(
                    action="pause",
                    message=outcome.message,
                    reload_flight_plan=True,
                )

            self.execution_state = ExecutionState.PAUSED
            return ExecutionDirective(action="pause", message=outcome.message)

        if result.action == InterventionAction.ABORT:
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self._current_intervention = None
            return ExecutionDirective(action="pause", message="Execution aborted")

        self._current_intervention = None
        return ExecutionDirective(action="pause")

    def consume_additional_iterations(self) -> int:
        """Consume additional iterations requested during intervention."""
        extra = self._additional_iterations
        self._additional_iterations = 0
        return extra

    def _transition_to_executing(self) -> None:
        journey = self.coordinator.project.journey
        if journey and journey.state in (
            JourneyState.CHART_REVIEW,
            JourneyState.LAND_REVIEW,
        ):
            self.coordinator.transition(JourneyState.FLY_READY)
        self.coordinator.transition(JourneyState.FLY_EXECUTING)

    def _mark_waypoint_failed(self) -> None:
        if self.current_waypoint:
            self.current_waypoint.status = WaypointStatus.FAILED
            self.coordinator.save_flight_plan()

    @staticmethod
    def _result_message(result: ExecutionResult) -> str:
        if result == ExecutionResult.INTERVENTION_NEEDED:
            return "Human intervention needed"
        if result == ExecutionResult.MAX_ITERATIONS:
            return "Max iterations reached"
        if result == ExecutionResult.FAILED:
            return "Execution failed"
        return "Execution failed"
