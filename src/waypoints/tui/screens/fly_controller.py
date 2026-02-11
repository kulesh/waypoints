"""Execution controller for Fly screen UI decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionNeededError,
    InterventionResult,
    InterventionType,
)
from waypoints.git import GitConfig
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.types import NextAction

if TYPE_CHECKING:
    from waypoints.orchestration import JourneyCoordinator


@dataclass
class StartDecision:
    """Decision for handling the start action."""

    action: Literal["rerun_selected", "run_current", "select_next"]
    waypoint: Waypoint | None


@dataclass
class PauseDecision:
    """Decision for handling pause action."""

    action: Literal["pause_pending", "paused"]


@dataclass
class WorkerDecision:
    """Decision derived from worker completion state."""

    action: Literal["handle_result", "handle_intervention", "handle_failure"]
    result: ExecutionResult | None = None
    intervention: Intervention | None = None


@dataclass
class ExecutionDecision:
    """Decision for handling execution results."""

    action: Literal["missing_waypoint", "apply_next_action"]
    completed_waypoint: Waypoint | None = None
    next_action: NextAction | None = None


@dataclass
class InterventionDisplayDecision:
    """Decision for displaying an intervention."""

    show_modal: bool
    should_mark_failed: bool


@dataclass
class InterventionResolutionDecision:
    """Decision for applying intervention modal result."""

    action: Literal["cancelled", "missing_context", "budget_wait", "apply_next_action"]
    next_action: NextAction | None = None
    retry_iterations: int = 0


class FlyController:
    """UI-agnostic branching decisions for Fly screen execution flow."""

    def __init__(self, coordinator: "JourneyCoordinator") -> None:
        self._coordinator = coordinator

    def start(
        self, selected_waypoint: Waypoint | None, current_waypoint: Waypoint | None
    ) -> StartDecision:
        if selected_waypoint and selected_waypoint.status in (
            WaypointStatus.FAILED,
            WaypointStatus.COMPLETE,
        ):
            return StartDecision(action="rerun_selected", waypoint=selected_waypoint)
        if current_waypoint and current_waypoint.status == WaypointStatus.FAILED:
            return StartDecision(action="run_current", waypoint=current_waypoint)
        return StartDecision(action="select_next", waypoint=None)

    def pause(self, is_running: bool) -> PauseDecision:
        if is_running:
            return PauseDecision(action="pause_pending")
        return PauseDecision(action="paused")

    def handle_worker_result(
        self,
        *,
        worker_error: BaseException | None,
        worker_result: ExecutionResult | None,
        pending_worker_intervention: Intervention | None,
    ) -> WorkerDecision:
        if isinstance(worker_error, InterventionNeededError):
            return WorkerDecision(
                action="handle_intervention",
                intervention=worker_error.intervention,
            )
        if worker_error is not None:
            return WorkerDecision(
                action="handle_failure",
                result=ExecutionResult.FAILED,
            )
        if worker_result == ExecutionResult.INTERVENTION_NEEDED:
            if pending_worker_intervention is not None:
                return WorkerDecision(
                    action="handle_intervention",
                    intervention=pending_worker_intervention,
                )
            return WorkerDecision(
                action="handle_failure",
                result=ExecutionResult.FAILED,
            )
        return WorkerDecision(action="handle_result", result=worker_result)

    def handle_execution_result(
        self,
        *,
        current_waypoint: Waypoint | None,
        result: ExecutionResult | None,
        git_config: GitConfig | None,
    ) -> ExecutionDecision:
        if current_waypoint is None or result is None:
            return ExecutionDecision(action="missing_waypoint")
        next_action = self._coordinator.handle_execution_result(
            current_waypoint,
            result,
            git_config=git_config,
        )
        return ExecutionDecision(
            action="apply_next_action",
            completed_waypoint=current_waypoint,
            next_action=next_action,
        )

    def handle_intervention_display(
        self, intervention: Intervention, current_waypoint: Waypoint | None
    ) -> InterventionDisplayDecision:
        presentation = self._coordinator.classify_intervention(intervention)
        return InterventionDisplayDecision(
            show_modal=presentation.show_modal,
            should_mark_failed=(
                current_waypoint is not None
                and intervention.type != InterventionType.BUDGET_EXCEEDED
            ),
        )

    def handle_intervention_result(
        self,
        *,
        result: InterventionResult | None,
        current_intervention: Intervention | None,
    ) -> InterventionResolutionDecision:
        if result is None:
            return InterventionResolutionDecision(action="cancelled")
        if current_intervention is None:
            return InterventionResolutionDecision(action="missing_context")
        if (
            result.action == InterventionAction.WAIT
            and current_intervention.type == InterventionType.BUDGET_EXCEEDED
        ):
            return InterventionResolutionDecision(action="budget_wait")

        next_action = self._coordinator.handle_intervention(
            current_intervention,
            result.action,
            additional_iterations=result.additional_iterations,
            rollback_ref=result.rollback_ref,
            rollback_tag=result.rollback_tag,
        )
        return InterventionResolutionDecision(
            action="apply_next_action",
            next_action=next_action,
            retry_iterations=(
                result.additional_iterations
                if result.action == InterventionAction.RETRY
                else 0
            ),
        )
