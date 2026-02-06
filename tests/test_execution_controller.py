"""Tests for ExecutionController behavior."""

from __future__ import annotations

from pathlib import Path

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionResult,
    InterventionType,
)
from waypoints.fly.state import ExecutionState
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.journey import Journey, JourneyState
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import ExecutionController, JourneyCoordinator


class DummyProject:
    """Minimal project stub for execution controller tests."""

    def __init__(self, *, journey_state: JourneyState) -> None:
        self.slug = "test-project"
        self.journey = Journey(state=journey_state, project_slug=self.slug)

    def get_path(self) -> Path:
        return Path("/tmp/test-project")

    def save(self) -> None:
        return None

    def transition_journey(
        self, target: JourneyState, reason: str | None = None
    ) -> None:
        if self.journey is None:
            self.journey = Journey.new(self.slug)
        self.journey = self.journey.transition(target, reason=reason)


def make_controller(
    *,
    journey_state: JourneyState,
    flight_plan: FlightPlan,
    current_waypoint: Waypoint | None = None,
) -> ExecutionController:
    project = DummyProject(journey_state=journey_state)
    coordinator = JourneyCoordinator(project=project, flight_plan=flight_plan)
    coordinator.current_waypoint = current_waypoint
    controller = ExecutionController(coordinator)
    return controller


def test_start_retries_failed_selected() -> None:
    flight_plan = FlightPlan()
    failed = Waypoint(
        id="WP-001",
        title="Failed",
        objective="Fix it",
        status=WaypointStatus.FAILED,
    )
    flight_plan.add_waypoint(failed)

    controller = make_controller(
        journey_state=JourneyState.FLY_PAUSED,
        flight_plan=flight_plan,
        current_waypoint=failed,
    )

    directive = controller.start(failed)

    assert directive.action == "execute"
    assert controller.execution_state == ExecutionState.RUNNING
    assert failed.status == WaypointStatus.PENDING
    assert controller.coordinator.project.journey.state == JourneyState.FLY_EXECUTING


def test_handle_success_transitions_to_land() -> None:
    flight_plan = FlightPlan()
    waypoint = Waypoint(
        id="WP-001",
        title="Only",
        objective="Complete",
        status=WaypointStatus.PENDING,
    )
    flight_plan.add_waypoint(waypoint)

    controller = make_controller(
        journey_state=JourneyState.FLY_EXECUTING,
        flight_plan=flight_plan,
        current_waypoint=waypoint,
    )
    controller.execution_state = ExecutionState.RUNNING

    directive = controller.handle_execution_result(ExecutionResult.SUCCESS)

    assert directive.action == "land"
    assert waypoint.status == WaypointStatus.COMPLETE
    assert waypoint.completed_at is not None
    assert controller.coordinator.project.journey.state == JourneyState.LAND_REVIEW


def test_prepare_intervention_marks_failed() -> None:
    flight_plan = FlightPlan()
    waypoint = Waypoint(
        id="WP-002",
        title="Needs help",
        objective="Intervene",
        status=WaypointStatus.PENDING,
    )
    flight_plan.add_waypoint(waypoint)

    controller = make_controller(
        journey_state=JourneyState.FLY_EXECUTING,
        flight_plan=flight_plan,
        current_waypoint=waypoint,
    )

    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=1,
        max_iterations=10,
        error_summary="boom",
    )

    directive = controller.prepare_intervention(intervention)

    assert directive.action == "intervention"
    assert controller.execution_state == ExecutionState.INTERVENTION
    assert waypoint.status == WaypointStatus.FAILED
    assert controller.coordinator.project.journey.state == JourneyState.FLY_INTERVENTION


def test_resolve_intervention_skip_selects_next() -> None:
    flight_plan = FlightPlan()
    first = Waypoint(
        id="WP-001",
        title="First",
        objective="Skip",
        status=WaypointStatus.PENDING,
    )
    second = Waypoint(
        id="WP-002",
        title="Second",
        objective="Next",
        status=WaypointStatus.PENDING,
    )
    flight_plan.add_waypoint(first)
    flight_plan.add_waypoint(second)

    controller = make_controller(
        journey_state=JourneyState.FLY_INTERVENTION,
        flight_plan=flight_plan,
        current_waypoint=first,
    )

    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=first,
        iteration=2,
        max_iterations=10,
        error_summary="skip",
    )
    controller.prepare_intervention(intervention)

    directive = controller.resolve_intervention(
        InterventionResult(action=InterventionAction.SKIP)
    )

    assert first.status == WaypointStatus.SKIPPED
    assert directive.action == "execute"
    assert directive.waypoint == second
    assert controller.execution_state == ExecutionState.RUNNING
