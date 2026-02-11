"""Tests for FlyScreen status bar behavior."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import (
    Intervention,
    InterventionNeededError,
    InterventionType,
)
from waypoints.fly.types import ExecutionContext
from waypoints.models import JourneyState
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.screens.fly import (
    ExecutionLogViewMode,
    ExecutionState,
    FlyScreen,
    WaypointDetailPanel,
)


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


class TestExecutionLogViewMode:
    """Tests for WaypointDetailPanel execution log mode behavior."""

    def test_default_log_view_mode_is_raw(self) -> None:
        panel = WaypointDetailPanel(project=SimpleNamespace(), flight_plan=FlightPlan())
        assert panel.log_view_mode == ExecutionLogViewMode.RAW

    def test_toggle_log_view_mode_switches_between_raw_and_summary(self) -> None:
        panel = WaypointDetailPanel(project=SimpleNamespace(), flight_plan=FlightPlan())
        panel._update_log_view_label = lambda: None  # type: ignore[method-assign]

        assert panel.toggle_log_view_mode() == ExecutionLogViewMode.SUMMARY
        assert panel.log_view_mode == ExecutionLogViewMode.SUMMARY

        assert panel.toggle_log_view_mode() == ExecutionLogViewMode.RAW
        assert panel.log_view_mode == ExecutionLogViewMode.RAW

    def test_build_raw_entry_payload_preserves_metadata_and_defaults(self) -> None:
        panel = WaypointDetailPanel(project=SimpleNamespace(), flight_plan=FlightPlan())
        entry = SimpleNamespace(
            entry_type="error",
            content="boom",
            iteration=3,
            timestamp=datetime(2026, 2, 7, 1, 2, 3, tzinfo=UTC),
            metadata={"foo": "bar"},
        )

        payload = panel._build_raw_entry_payload(entry)

        assert payload["type"] == "error"
        assert payload["iteration"] == 3
        assert payload["error"] == "boom"
        assert payload["foo"] == "bar"

    def test_log_tool_call_entry_renders_validation_timeout_events(self) -> None:
        panel = WaypointDetailPanel(project=SimpleNamespace(), flight_plan=FlightPlan())

        class _FakeLog:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def write_log(self, line: str) -> None:
                self.lines.append(line)

            def write(self, line: str) -> None:
                self.lines.append(str(line))

        fake_log = _FakeLog()
        entry = SimpleNamespace(
            metadata={
                "tool_name": "ValidationCommand",
                "tool_input": {
                    "command": "cargo clippy -- -D warnings",
                    "category": "lint",
                    "attempts": 2,
                    "timed_out": True,
                    "timeout_seconds": 900.0,
                    "signals": ["SIGTERM", "SIGKILL"],
                    "timeout_events": [
                        {
                            "event_type": "warning",
                            "attempt": 1,
                            "timeout_seconds": 900.0,
                            "detail": "Timeout threshold approaching",
                        },
                        {
                            "event_type": "retry",
                            "attempt": 1,
                            "timeout_seconds": 900.0,
                            "detail": "Retrying after timeout with backoff",
                        },
                    ],
                },
                "tool_output": "exit_code=124",
            }
        )

        panel._log_tool_call_entry(fake_log, entry)
        rendered = "\n".join(fake_log.lines)
        assert "cargo clippy -- -D warnings" in rendered
        assert "timed_out=yes" in rendered
        assert "signals: SIGTERM -> SIGKILL" in rendered
        assert "timeout: warning" in rendered
        assert "timeout: retry" in rendered

    def test_apply_agent_progress_updates_monitor_from_guidance_packet(self) -> None:
        panel = WaypointDetailPanel(project=SimpleNamespace(), flight_plan=FlightPlan())
        ctx = ExecutionContext(
            waypoint=Waypoint(id="WP-001", title="Title", objective="Objective"),
            iteration=1,
            total_iterations=10,
            step="protocol_artifact",
            output="builder:guidance_packet",
            metadata={
                "artifact": {
                    "artifact_type": "guidance_packet",
                    "produced_by_role": "builder",
                    "role_constraints": [
                        "Implement only within waypoint scope.",
                        "Run required validations before completion claim.",
                    ],
                    "stop_conditions": [
                        (
                            "Do not emit completion marker while clarification is "
                            "unresolved."
                        )
                    ],
                }
            },
        )

        panel.apply_agent_progress(ctx)

        assert panel._orchestrator_expectations == (
            "Implement only within waypoint scope.",
            "Run required validations before completion claim.",
        )
        assert panel._orchestrator_stop_conditions == (
            "Do not emit completion marker while clarification is unresolved.",
        )
        assert panel._agent_status["builder"] == "Received orchestrator guidance"

    def test_format_protocol_artifact_summary_context_envelope(self) -> None:
        panel = WaypointDetailPanel(project=SimpleNamespace(), flight_plan=FlightPlan())
        summary = panel._format_protocol_artifact_summary(
            {
                "artifact_type": "context_envelope",
                "prompt_budget_chars": 24000,
                "overflowed": True,
                "slices": [{"name": "memory"}, {"name": "policy"}],
            }
        )

        assert "budget=24000 chars" in summary
        assert "slices=2" in summary
        assert "overflowed=yes" in summary


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


class TestBudgetWaitAutoResume:
    """Tests for budget-wait countdown auto-resume behavior."""

    def test_budget_wait_tick_auto_resumes_failed_waypoint(self) -> None:
        class MockProject:
            def __init__(self) -> None:
                self.journey = SimpleNamespace(state=JourneyState.FLY_PAUSED)

            def get_path(self) -> Path:
                return Path("/tmp/test-project")

        waypoint = Waypoint(
            id="WP-010",
            title="Budget paused waypoint",
            objective="Resume after reset",
            status=WaypointStatus.FAILED,
        )
        flight_plan = FlightPlan(waypoints=[waypoint])
        screen = FlyScreen(  # type: ignore[arg-type]
            project=MockProject(),
            flight_plan=flight_plan,
            spec="spec",
        )

        marked_statuses: list[WaypointStatus] = []
        transitions: list[JourneyState] = []
        notifications: list[str] = []
        executed: list[bool] = []

        screen.current_waypoint = None
        screen.watch_execution_state = lambda _state: None  # type: ignore[method-assign]
        screen.execution_state = ExecutionState.PAUSED
        screen._budget_resume_at = datetime.now(UTC)
        screen._budget_resume_waypoint_id = waypoint.id
        screen._budget_wait_timer = None
        screen.coordinator.mark_waypoint_status = (  # type: ignore[method-assign]
            lambda _wp, status: marked_statuses.append(status)
        )
        screen.coordinator.transition = (  # type: ignore[method-assign]
            lambda state: transitions.append(state)
        )
        screen.notify = lambda message, **_kwargs: notifications.append(str(message))  # type: ignore[method-assign]
        screen._execute_current_waypoint = lambda: executed.append(True)  # type: ignore[method-assign]
        screen.query_one = lambda *_args, **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
            set_normal=lambda: None
        )

        screen._on_budget_wait_tick()

        assert marked_statuses == [WaypointStatus.PENDING]
        assert screen.current_waypoint == waypoint
        assert transitions == [JourneyState.FLY_EXECUTING]
        assert screen.execution_state == ExecutionState.RUNNING
        assert executed == [True]
        assert notifications[-1] == "Budget reset window reached. Resuming execution."

    def test_budget_wait_tick_warns_when_no_waypoint_can_resume(self) -> None:
        class MockProject:
            def __init__(self) -> None:
                self.journey = SimpleNamespace(state=JourneyState.FLY_PAUSED)

            def get_path(self) -> Path:
                return Path("/tmp/test-project")

        flight_plan = FlightPlan()
        screen = FlyScreen(  # type: ignore[arg-type]
            project=MockProject(),
            flight_plan=flight_plan,
            spec="spec",
        )

        notifications: list[tuple[str, str | None]] = []
        selected: list[bool] = []
        executed: list[bool] = []

        screen.current_waypoint = None
        screen.watch_execution_state = lambda _state: None  # type: ignore[method-assign]
        screen.execution_state = ExecutionState.PAUSED
        screen._budget_resume_at = datetime.now(UTC)
        screen._budget_resume_waypoint_id = None
        screen._budget_wait_timer = None
        screen._select_next_waypoint = (
            lambda include_in_progress=False: selected.append(  # type: ignore[method-assign]
                include_in_progress
            )
        )
        screen.notify = lambda message, **kwargs: notifications.append(  # type: ignore[method-assign]
            (str(message), kwargs.get("severity"))
        )
        screen._execute_current_waypoint = lambda: executed.append(True)  # type: ignore[method-assign]

        screen._on_budget_wait_tick()

        assert selected == [True]
        assert screen.current_waypoint is None
        assert screen.execution_state == ExecutionState.PAUSED
        assert executed == []
        assert notifications[-1] == (
            "Budget reset reached, but no waypoint is ready to resume.",
            "warning",
        )


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


def test_action_start_reruns_completed_waypoint_from_land_state() -> None:
    waypoint = Waypoint(
        id="WP-001",
        title="Completed task",
        objective="Done",
        status=WaypointStatus.COMPLETE,
    )
    waypoint.completed_at = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)

    transitions: list[JourneyState] = []
    marked_statuses: list[WaypointStatus] = []
    notifications: list[str] = []
    shown_waypoints: list[str] = []
    executed: list[bool] = []

    list_panel = SimpleNamespace(selected_waypoint=waypoint)
    detail_panel = SimpleNamespace(
        show_waypoint=lambda selected, **_kwargs: shown_waypoints.append(selected.id)
    )

    class MockProject:
        def __init__(self) -> None:
            self.journey = SimpleNamespace(state=JourneyState.LAND_REVIEW)

        def get_path(self) -> Path:
            return Path("/tmp/test-project")

    project = MockProject()
    screen = FlyScreen(project=project, flight_plan=FlightPlan(), spec="spec")  # type: ignore[arg-type]

    screen.coordinator.mark_waypoint_status = (  # type: ignore[method-assign]
        lambda _wp, status: marked_statuses.append(status)
    )
    screen.coordinator.transition = (  # type: ignore[method-assign]
        lambda state: transitions.append(state)
    )
    screen._clear_budget_wait = lambda: None  # type: ignore[method-assign]
    screen.watch_execution_state = lambda _state: None  # type: ignore[method-assign]
    screen._refresh_waypoint_list = lambda: None  # type: ignore[method-assign]
    screen._execute_current_waypoint = lambda: executed.append(True)  # type: ignore[method-assign]
    screen._get_waypoint_cost = lambda _waypoint_id: 0.0  # type: ignore[method-assign]
    screen._get_waypoint_tokens = lambda _waypoint_id: (0, 0, False)  # type: ignore[method-assign]
    screen._get_waypoint_cached_tokens_in = (  # type: ignore[method-assign]
        lambda _waypoint_id: (0, False)
    )
    screen.notify = lambda message, **_kwargs: notifications.append(str(message))  # type: ignore[method-assign]
    screen.query_one = lambda selector, *_args: (  # type: ignore[method-assign]
        list_panel if selector == "#waypoint-list" else detail_panel
    )

    screen.action_start()

    assert waypoint.status == WaypointStatus.PENDING
    assert waypoint.completed_at is None
    assert marked_statuses == [WaypointStatus.PENDING]
    assert transitions == [JourneyState.FLY_READY, JourneyState.FLY_EXECUTING]
    assert notifications == ["Re-running WP-001"]
    assert shown_waypoints == ["WP-001"]
    assert screen.coordinator.current_waypoint == waypoint
    assert screen.execution_state == ExecutionState.RUNNING
    assert executed == [True]
