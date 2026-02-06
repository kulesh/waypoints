"""Tests for FlyScreen status bar behavior."""

import asyncio

import pytest

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import (
    Intervention,
    InterventionNeededError,
    InterventionType,
)
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.screens.fly import ExecutionState, FlyScreen


def make_test_screen(flight_plan: FlightPlan) -> FlyScreen:
    """Create a minimal FlyScreen for testing without full TUI initialization.

    This sets up the required coordinator but bypasses full Screen initialization.
    """
    screen = FlyScreen.__new__(FlyScreen)
    screen.flight_plan = flight_plan

    # Create a mock project for the coordinator
    class MockProject:
        def get_path(self):
            from pathlib import Path

            return Path("/tmp/test-project")

    # Set up coordinator - needed for current_waypoint property
    screen.coordinator = JourneyCoordinator(
        project=MockProject(),  # type: ignore
        flight_plan=flight_plan,
    )
    return screen


class TestStatusBarMessage:
    """Tests for the _get_state_message method."""

    @pytest.fixture
    def flight_plan(self) -> FlightPlan:
        """Create a sample flight plan with mixed statuses."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Completed Task",
                objective="Already done",
                status=WaypointStatus.COMPLETE,
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-003b",
                title="Phase Screen Scaffolding",
                objective="Create scaffolding",
                status=WaypointStatus.PENDING,
            )
        )
        return fp

    @pytest.fixture
    def all_complete_flight_plan(self) -> FlightPlan:
        """Create a flight plan where all waypoints are complete."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="First Task",
                objective="First",
                status=WaypointStatus.COMPLETE,
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="Second Task",
                objective="Second",
                status=WaypointStatus.COMPLETE,
            )
        )
        return fp

    def test_idle_with_current_waypoint_shows_waypoint_info(
        self, flight_plan: FlightPlan
    ):
        """Test IDLE state with current_waypoint shows waypoint ID and title."""
        screen = make_test_screen(flight_plan)
        screen.current_waypoint = flight_plan.waypoints[1]  # WP-003b

        # Test the message - pass state as argument, don't set as property
        message = screen._get_state_message(ExecutionState.IDLE)
        assert message == "Press 'r' to run WP-003b: Phase Screen Scaffolding"

    def test_idle_without_current_waypoint(self, flight_plan: FlightPlan):
        """Test that IDLE state without current_waypoint shows appropriate message."""
        screen = make_test_screen(flight_plan)
        screen.current_waypoint = None

        message = screen._get_state_message(ExecutionState.IDLE)
        assert message == "No waypoints ready to run"

    def test_done_state_all_complete(self, all_complete_flight_plan: FlightPlan):
        """Test DONE state when all waypoints are complete shows success message."""
        screen = make_test_screen(all_complete_flight_plan)
        screen.current_waypoint = None

        message = screen._get_state_message(ExecutionState.DONE)
        assert message == "All waypoints complete!"

    def test_done_state_with_pending_shows_waiting(self, flight_plan: FlightPlan):
        """Test DONE state with pending waypoints shows waiting count."""
        screen = make_test_screen(flight_plan)
        screen.current_waypoint = None

        message = screen._get_state_message(ExecutionState.DONE)
        assert "waypoint(s) waiting" in message

    def test_done_state_with_failed_shows_failure(self):
        """Test DONE state with failed waypoints shows failure count."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Failed Task",
                objective="Failed",
                status=WaypointStatus.FAILED,
            )
        )

        screen = make_test_screen(fp)
        screen.current_waypoint = None

        message = screen._get_state_message(ExecutionState.DONE)
        assert "waypoint(s) failed" in message

    def test_done_state_with_blocked_shows_blocked(self):
        """Test DONE state with blocked waypoints shows blocked count."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="Failed Task",
                objective="Failed",
                status=WaypointStatus.FAILED,
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="Blocked Task",
                objective="Blocked by failed",
                status=WaypointStatus.PENDING,
                dependencies=["WP-001"],
            )
        )

        screen = make_test_screen(fp)
        screen.current_waypoint = None

        message = screen._get_state_message(ExecutionState.DONE)
        assert "blocked by failures" in message

    def test_running_state(self, flight_plan: FlightPlan):
        """Test that RUNNING state shows executing message."""
        screen = make_test_screen(flight_plan)
        screen.current_waypoint = flight_plan.waypoints[1]

        message = screen._get_state_message(ExecutionState.RUNNING)
        assert message == "Executing waypoint..."

    def test_paused_with_waypoint(self, flight_plan: FlightPlan):
        """Test that PAUSED state with waypoint shows waypoint ID."""
        screen = make_test_screen(flight_plan)
        screen.current_waypoint = flight_plan.waypoints[1]

        message = screen._get_state_message(ExecutionState.PAUSED)
        assert message == "Paused. Press 'r' to run WP-003b"

    def test_intervention_with_waypoint(self, flight_plan: FlightPlan):
        """Test that INTERVENTION state with waypoint shows waypoint ID."""
        screen = make_test_screen(flight_plan)
        screen.current_waypoint = flight_plan.waypoints[1]

        message = screen._get_state_message(ExecutionState.INTERVENTION)
        assert message == "Intervention needed for WP-003b"

    def test_long_title_truncation(self, flight_plan: FlightPlan):
        """Test that long titles are truncated."""
        long_wp = Waypoint(
            id="WP-999",
            title="This is a very long title that should be truncated to fit nicely",
            objective="Test",
            status=WaypointStatus.PENDING,
        )

        screen = make_test_screen(flight_plan)
        screen.current_waypoint = long_wp

        message = screen._get_state_message(ExecutionState.IDLE)
        assert "..." in message
        assert message.startswith("Press 'r' to run WP-999: This is a very long")
        # Title should be truncated to 40 chars + "..."
        assert "This is a very long title that should be" in message

    def test_paused_with_failed_waypoint_shows_continue_message(self):
        """Test that PAUSED state with failed waypoint shows accurate message."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-003a",
                title="Failed Task",
                objective="This task failed",
                status=WaypointStatus.FAILED,
            )
        )

        screen = make_test_screen(fp)
        screen.current_waypoint = fp.waypoints[0]

        message = screen._get_state_message(ExecutionState.PAUSED)
        # Should NOT say "Press 'r' to run WP-003a" since it failed
        assert "WP-003a failed" in message
        assert "continue" in message


class TestWorkerInterventionHandling:
    """Tests for worker-safe intervention flow."""

    def test_run_executor_converts_intervention_error_to_result(self) -> None:
        """Interventions should not escape worker execution as exceptions."""

        class FakeExecutor:
            async def execute(self) -> ExecutionResult:
                waypoint = Waypoint(
                    id="WP-001",
                    title="Test",
                    objective="Test objective",
                    status=WaypointStatus.IN_PROGRESS,
                )
                intervention = Intervention(
                    type=InterventionType.BUDGET_EXCEEDED,
                    waypoint=waypoint,
                    iteration=1,
                    max_iterations=10,
                    error_summary="Budget reached",
                )
                raise InterventionNeededError(intervention)

        fp = FlightPlan()
        screen = make_test_screen(fp)
        # Inject fake executor through the coordinator's fly phase
        screen.coordinator._fly._active_executor = FakeExecutor()  # type: ignore[assignment]

        result = asyncio.run(screen._run_executor())

        assert result == ExecutionResult.INTERVENTION_NEEDED
        pending = screen.coordinator.take_worker_intervention()
        assert pending is not None
        assert pending.type == InterventionType.BUDGET_EXCEEDED


class TestSelectNextWaypoint:
    """Tests for the waypoint selection logic."""

    @pytest.fixture
    def flight_plan_with_deps(self) -> FlightPlan:
        """Create a flight plan with dependencies."""
        fp = FlightPlan()
        fp.add_waypoint(
            Waypoint(
                id="WP-001",
                title="First",
                objective="First task",
                status=WaypointStatus.COMPLETE,
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-002",
                title="Second",
                objective="Second task",
                status=WaypointStatus.PENDING,
                dependencies=["WP-001"],
            )
        )
        fp.add_waypoint(
            Waypoint(
                id="WP-003",
                title="Third",
                objective="Third task",
                status=WaypointStatus.PENDING,
                dependencies=["WP-002"],
            )
        )
        return fp

    def test_selection_logic_picks_first_pending_with_met_deps(
        self, flight_plan_with_deps: FlightPlan
    ):
        """Test that selection picks first pending waypoint with met dependencies."""
        # Test the core selection logic without full screen initialization
        selected = None
        for wp in flight_plan_with_deps.waypoints:
            if wp.status != WaypointStatus.PENDING:
                continue
            if flight_plan_with_deps.is_epic(wp.id):
                continue
            unmet = [
                d
                for d in wp.dependencies
                if (dep := flight_plan_with_deps.get_waypoint(d)) is None
                or dep.status not in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED)
            ]
            if not unmet:
                selected = wp
                break

        assert selected is not None
        assert selected.id == "WP-002"

    def test_selection_skips_blocked_waypoints(self, flight_plan_with_deps: FlightPlan):
        """Test that blocked waypoints are skipped."""
        # WP-003 depends on WP-002 which is PENDING, so WP-003 should be blocked
        selected = None
        for wp in flight_plan_with_deps.waypoints:
            if wp.status != WaypointStatus.PENDING:
                continue
            if flight_plan_with_deps.is_epic(wp.id):
                continue
            unmet = [
                d
                for d in wp.dependencies
                if (dep := flight_plan_with_deps.get_waypoint(d)) is None
                or dep.status not in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED)
            ]
            if not unmet:
                selected = wp
                break

        # Should select WP-002, not WP-003
        assert selected is not None
        assert selected.id == "WP-002"
        assert selected.id != "WP-003"
