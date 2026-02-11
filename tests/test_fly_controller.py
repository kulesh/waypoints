"""Unit tests for FlyController decision logic."""

from dataclasses import dataclass

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionNeededError,
    InterventionResult,
    InterventionType,
)
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.types import InterventionPresentation, NextAction
from waypoints.tui.screens.fly_controller import FlyController


def _waypoint(
    waypoint_id: str = "WP-001",
    *,
    status: WaypointStatus = WaypointStatus.PENDING,
) -> Waypoint:
    return Waypoint(
        id=waypoint_id,
        title=f"Waypoint {waypoint_id}",
        objective="Objective",
        status=status,
    )


def _intervention(
    *,
    intervention_type: InterventionType = InterventionType.EXECUTION_ERROR,
) -> Intervention:
    return Intervention(
        type=intervention_type,
        waypoint=_waypoint(status=WaypointStatus.FAILED),
        iteration=3,
        max_iterations=10,
        error_summary="failure",
    )


@dataclass
class _FakeCoordinator:
    next_action: NextAction
    show_modal: bool = True

    def handle_execution_result(
        self,
        waypoint: Waypoint,
        result: ExecutionResult,
        git_config: object | None = None,
    ) -> NextAction:
        del waypoint, result, git_config
        return self.next_action

    def classify_intervention(
        self, intervention: Intervention
    ) -> InterventionPresentation:
        return InterventionPresentation(
            show_modal=self.show_modal, intervention=intervention
        )

    def handle_intervention(
        self,
        intervention: Intervention,
        action: InterventionAction,
        additional_iterations: int = 5,
        rollback_ref: str | None = None,
        rollback_tag: str | None = None,
    ) -> NextAction:
        del intervention, action, additional_iterations, rollback_ref, rollback_tag
        return self.next_action


class TestFlyControllerStartAndPause:
    def test_start_uses_selected_failed_waypoint(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)
        selected = _waypoint(status=WaypointStatus.FAILED)

        decision = controller.start(selected_waypoint=selected, current_waypoint=None)

        assert decision.action == "rerun_selected"
        assert decision.waypoint is selected

    def test_start_uses_current_failed_waypoint(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)
        current = _waypoint(status=WaypointStatus.FAILED)

        decision = controller.start(selected_waypoint=None, current_waypoint=current)

        assert decision.action == "run_current"
        assert decision.waypoint is current

    def test_start_selects_next_when_nothing_failed(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)

        decision = controller.start(
            selected_waypoint=None,
            current_waypoint=_waypoint(status=WaypointStatus.PENDING),
        )

        assert decision.action == "select_next"
        assert decision.waypoint is None

    def test_pause_decision(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)

        assert controller.pause(is_running=True).action == "pause_pending"
        assert controller.pause(is_running=False).action == "paused"


class TestFlyControllerWorkerResult:
    def test_worker_intervention_error_maps_to_intervention_action(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)
        intervention = _intervention()

        decision = controller.handle_worker_result(
            worker_error=InterventionNeededError(intervention),
            worker_result=None,
            pending_worker_intervention=None,
        )

        assert decision.action == "handle_intervention"
        assert decision.intervention == intervention

    def test_generic_worker_error_maps_to_failure(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)

        decision = controller.handle_worker_result(
            worker_error=RuntimeError("boom"),
            worker_result=None,
            pending_worker_intervention=None,
        )

        assert decision.action == "handle_failure"
        assert decision.result == ExecutionResult.FAILED

    def test_worker_intervention_result_uses_pending_intervention(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)
        intervention = _intervention()

        decision = controller.handle_worker_result(
            worker_error=None,
            worker_result=ExecutionResult.INTERVENTION_NEEDED,
            pending_worker_intervention=intervention,
        )

        assert decision.action == "handle_intervention"
        assert decision.intervention == intervention

    def test_worker_intervention_result_without_context_fails(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)

        decision = controller.handle_worker_result(
            worker_error=None,
            worker_result=ExecutionResult.INTERVENTION_NEEDED,
            pending_worker_intervention=None,
        )

        assert decision.action == "handle_failure"
        assert decision.result == ExecutionResult.FAILED

    def test_worker_success_passthrough(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)

        decision = controller.handle_worker_result(
            worker_error=None,
            worker_result=ExecutionResult.SUCCESS,
            pending_worker_intervention=None,
        )

        assert decision.action == "handle_result"
        assert decision.result == ExecutionResult.SUCCESS


class TestFlyControllerExecutionAndIntervention:
    def test_handle_execution_result_missing_waypoint(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)

        decision = controller.handle_execution_result(
            current_waypoint=None,
            result=ExecutionResult.SUCCESS,
            git_config=None,
        )

        assert decision.action == "missing_waypoint"
        assert decision.next_action is None

    def test_handle_execution_result_applies_next_action(self) -> None:
        next_action = NextAction(action="continue", waypoint=_waypoint("WP-002"))
        coordinator = _FakeCoordinator(next_action=next_action)
        controller = FlyController(coordinator)
        waypoint = _waypoint("WP-001")

        decision = controller.handle_execution_result(
            current_waypoint=waypoint,
            result=ExecutionResult.SUCCESS,
            git_config=None,
        )

        assert decision.action == "apply_next_action"
        assert decision.completed_waypoint is waypoint
        assert decision.next_action == next_action

    def test_intervention_display_uses_classification_and_budget_rule(self) -> None:
        coordinator = _FakeCoordinator(
            next_action=NextAction(action="pause"), show_modal=False
        )
        controller = FlyController(coordinator)
        waypoint = _waypoint("WP-001")

        budget_decision = controller.handle_intervention_display(
            _intervention(intervention_type=InterventionType.BUDGET_EXCEEDED),
            waypoint,
        )
        non_budget_decision = controller.handle_intervention_display(
            _intervention(intervention_type=InterventionType.EXECUTION_ERROR),
            waypoint,
        )

        assert budget_decision.show_modal is False
        assert budget_decision.should_mark_failed is False
        assert non_budget_decision.show_modal is False
        assert non_budget_decision.should_mark_failed is True

    def test_intervention_result_cancelled_and_missing_context(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)

        cancelled = controller.handle_intervention_result(
            result=None,
            current_intervention=None,
        )
        missing_context = controller.handle_intervention_result(
            result=InterventionResult(action=InterventionAction.RETRY),
            current_intervention=None,
        )

        assert cancelled.action == "cancelled"
        assert missing_context.action == "missing_context"

    def test_intervention_result_budget_wait(self) -> None:
        coordinator = _FakeCoordinator(next_action=NextAction(action="pause"))
        controller = FlyController(coordinator)
        budget_intervention = _intervention(
            intervention_type=InterventionType.BUDGET_EXCEEDED
        )

        decision = controller.handle_intervention_result(
            result=InterventionResult(action=InterventionAction.WAIT),
            current_intervention=budget_intervention,
        )

        assert decision.action == "budget_wait"
        assert decision.next_action is None

    def test_intervention_result_retry_tracks_additional_iterations(self) -> None:
        next_action = NextAction(action="continue", waypoint=_waypoint("WP-003"))
        coordinator = _FakeCoordinator(next_action=next_action)
        controller = FlyController(coordinator)
        intervention = _intervention()

        decision = controller.handle_intervention_result(
            result=InterventionResult(
                action=InterventionAction.RETRY,
                additional_iterations=7,
            ),
            current_intervention=intervention,
        )

        assert decision.action == "apply_next_action"
        assert decision.next_action == next_action
        assert decision.retry_iterations == 7
