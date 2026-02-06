"""Tests for FLY orchestration service decisions."""

from pathlib import Path

from waypoints.fly.executor import ExecutionResult
from waypoints.models import JourneyState
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.orchestration.fly_service import FlyService


class MockProject:
    """Minimal project mock for coordinator/service tests."""

    def __init__(self, path: Path | None = None):
        self._path = path or Path("/tmp/test-project")

    def get_path(self) -> Path:
        return self._path


def test_select_next_waypoint_returns_continue_when_work_is_ready() -> None:
    fp = FlightPlan()
    fp.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Done",
            objective="Already complete",
            status=WaypointStatus.COMPLETE,
        )
    )
    fp.add_waypoint(
        Waypoint(
            id="WP-002",
            title="Ready",
            objective="Should run next",
            status=WaypointStatus.PENDING,
            dependencies=["WP-001"],
        )
    )
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=fp)  # type: ignore
    service = FlyService(coordinator)

    action = service.select_next_waypoint_action()

    assert action.action == "continue"
    assert action.waypoint is not None
    assert action.waypoint.id == "WP-002"


def test_select_next_waypoint_returns_complete_when_all_done() -> None:
    fp = FlightPlan()
    fp.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Done",
            objective="Complete",
            status=WaypointStatus.COMPLETE,
        )
    )
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=fp)  # type: ignore
    service = FlyService(coordinator)

    action = service.select_next_waypoint_action()

    assert action.action == "complete"
    assert action.message == "All waypoints complete!"


def test_select_next_waypoint_returns_pause_for_blocked_plan() -> None:
    fp = FlightPlan()
    fp.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Failed",
            objective="Broken dependency",
            status=WaypointStatus.FAILED,
        )
    )
    fp.add_waypoint(
        Waypoint(
            id="WP-002",
            title="Blocked",
            objective="Depends on failure",
            status=WaypointStatus.PENDING,
            dependencies=["WP-001"],
        )
    )
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=fp)  # type: ignore
    service = FlyService(coordinator)

    action = service.select_next_waypoint_action()

    assert action.action == "pause"
    assert action.message is not None
    assert "blocked" in action.message


def test_select_next_waypoint_returns_pause_for_waiting_waypoints() -> None:
    fp = FlightPlan()
    fp.add_waypoint(
        Waypoint(
            id="WP-002",
            title="Waiting",
            objective="Has unmet deps but no failures",
            status=WaypointStatus.PENDING,
            dependencies=["WP-001"],
        )
    )
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=fp)  # type: ignore
    service = FlyService(coordinator)

    action = service.select_next_waypoint_action()

    assert action.action == "pause"
    assert action.message == "1 waypoint(s) waiting"


def test_select_next_waypoint_returns_pause_when_only_failed_remain() -> None:
    fp = FlightPlan()
    fp.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Failed",
            objective="Still failed",
            status=WaypointStatus.FAILED,
        )
    )
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=fp)  # type: ignore
    service = FlyService(coordinator)

    action = service.select_next_waypoint_action()

    assert action.action == "pause"
    assert action.message == "Only failed waypoints remain (1)"


def test_select_next_waypoint_can_resume_failed_when_requested() -> None:
    fp = FlightPlan()
    fp.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Failed",
            objective="Retry candidate",
            status=WaypointStatus.FAILED,
        )
    )
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=fp)  # type: ignore
    service = FlyService(coordinator)

    action = service.select_next_waypoint_action(include_failed=True)

    assert action.action == "continue"
    assert action.waypoint is not None
    assert action.waypoint.id == "WP-001"


def test_resolve_non_success_result_for_intervention_needed() -> None:
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=FlightPlan())  # type: ignore
    service = FlyService(coordinator)

    policy = service.resolve_non_success_result(ExecutionResult.INTERVENTION_NEEDED)

    assert policy.log_method == "error"
    assert policy.log_message == "Human intervention needed"
    assert policy.mark_waypoint_failed is True
    assert policy.journey_state == JourneyState.FLY_INTERVENTION
    assert policy.execution_state == "intervention"
    assert policy.status_header_error is True
    assert policy.notification_message == "Waypoint needs human intervention"
    assert policy.notification_severity == "warning"


def test_resolve_non_success_result_for_max_iterations() -> None:
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=FlightPlan())  # type: ignore
    service = FlyService(coordinator)

    policy = service.resolve_non_success_result(ExecutionResult.MAX_ITERATIONS)

    assert policy.log_method == "error"
    assert policy.log_message == "Max iterations reached without completion"
    assert policy.mark_waypoint_failed is True
    assert policy.journey_state == JourneyState.FLY_INTERVENTION
    assert policy.execution_state == "intervention"
    assert policy.status_header_error is True
    assert policy.notification_message == "Max iterations reached"
    assert policy.notification_severity == "error"


def test_resolve_non_success_result_for_cancelled() -> None:
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=FlightPlan())  # type: ignore
    service = FlyService(coordinator)

    policy = service.resolve_non_success_result(ExecutionResult.CANCELLED)

    assert policy.log_method == "write"
    assert policy.log_message == "Execution cancelled"
    assert policy.mark_waypoint_failed is False
    assert policy.journey_state == JourneyState.FLY_PAUSED
    assert policy.execution_state == "paused"
    assert policy.status_header_error is False
    assert policy.notification_message is None
    assert policy.notification_severity is None


def test_resolve_non_success_result_for_failed_or_none_defaults_to_failed() -> None:
    coordinator = JourneyCoordinator(project=MockProject(), flight_plan=FlightPlan())  # type: ignore
    service = FlyService(coordinator)

    policy = service.resolve_non_success_result(None)

    assert policy.log_method == "error"
    assert policy.log_message == "Execution failed"
    assert policy.mark_waypoint_failed is True
    assert policy.journey_state == JourneyState.FLY_INTERVENTION
    assert policy.execution_state == "intervention"
    assert policy.status_header_error is True
    assert policy.notification_message == "Waypoint execution failed"
    assert policy.notification_severity == "error"
