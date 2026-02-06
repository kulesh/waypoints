"""Tests for FLY status message presenter logic."""

from collections.abc import Callable

from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.fly_presenter import build_state_message, build_status_line


def _status_provider(
    all_complete: bool,
    pending: int,
    failed: int,
    blocked: int,
) -> Callable[[], tuple[bool, int, int, int]]:
    return lambda: (all_complete, pending, failed, blocked)


def test_idle_with_current_waypoint_shows_waypoint_info() -> None:
    waypoint = Waypoint(
        id="WP-003b",
        title="Phase Screen Scaffolding",
        objective="Create scaffolding",
        status=WaypointStatus.PENDING,
    )
    message = build_state_message(
        state="idle",
        current_waypoint=waypoint,
        get_completion_status=_status_provider(False, 2, 0, 0),
    )
    assert message == "Press 'r' to run WP-003b: Phase Screen Scaffolding"


def test_idle_with_long_title_truncates() -> None:
    waypoint = Waypoint(
        id="WP-999",
        title="This is a very long title that should be truncated to fit nicely",
        objective="Test objective",
        status=WaypointStatus.PENDING,
    )
    message = build_state_message(
        state="idle",
        current_waypoint=waypoint,
        get_completion_status=_status_provider(False, 0, 0, 0),
    )
    assert "..." in message
    assert message.startswith("Press 'r' to run WP-999: This is a very long")


def test_idle_without_current_waypoint() -> None:
    message = build_state_message(
        state="idle",
        current_waypoint=None,
        get_completion_status=_status_provider(False, 0, 0, 0),
    )
    assert message == "No waypoints ready to run"


def test_running_does_not_require_completion_lookup() -> None:
    def fail_if_called() -> tuple[bool, int, int, int]:
        raise AssertionError("completion lookup should not be called")

    message = build_state_message(
        state="running",
        current_waypoint=None,
        get_completion_status=fail_if_called,
    )
    assert message == "Executing waypoint..."


def test_pause_pending_message() -> None:
    message = build_state_message(
        state="pause_pending",
        current_waypoint=None,
        get_completion_status=_status_provider(False, 0, 0, 0),
    )
    assert message == "Pausing after current waypoint..."


def test_paused_with_failed_waypoint_shows_continue_message() -> None:
    waypoint = Waypoint(
        id="WP-003a",
        title="Failed Task",
        objective="This task failed",
        status=WaypointStatus.FAILED,
    )
    message = build_state_message(
        state="paused",
        current_waypoint=waypoint,
        get_completion_status=_status_provider(False, 0, 1, 0),
    )
    assert message == "WP-003a failed. Press 'r' to continue"


def test_paused_with_pending_shows_waiting_message() -> None:
    message = build_state_message(
        state="paused",
        current_waypoint=None,
        get_completion_status=_status_provider(False, 3, 0, 0),
    )
    assert message == "Paused · 3 waypoint(s) waiting"


def test_done_all_complete() -> None:
    message = build_state_message(
        state="done",
        current_waypoint=None,
        get_completion_status=_status_provider(True, 0, 0, 0),
    )
    assert message == "All waypoints complete!"


def test_done_blocked_by_failures() -> None:
    message = build_state_message(
        state="done",
        current_waypoint=None,
        get_completion_status=_status_provider(False, 0, 1, 2),
    )
    assert message == "2 waypoint(s) blocked by failures"


def test_intervention_with_waypoint() -> None:
    waypoint = Waypoint(
        id="WP-123",
        title="Needs help",
        objective="Intervention path",
        status=WaypointStatus.FAILED,
    )
    message = build_state_message(
        state="intervention",
        current_waypoint=waypoint,
        get_completion_status=_status_provider(False, 0, 1, 0),
    )
    assert message == "Intervention needed for WP-123"


def test_build_status_line_running_includes_time_and_cost() -> None:
    line = build_status_line(
        host_label="HostVal: ON",
        message="Executing waypoint...",
        cost=1.234,
        elapsed_seconds=125,
    )
    assert line == "HostVal: ON    ⏱ 2:05 | $1.23    Executing waypoint..."


def test_build_status_line_idle_omits_zero_cost() -> None:
    line = build_status_line(
        host_label="HostVal: ON",
        message="No waypoints ready to run",
        cost=0.0,
    )
    assert line == "HostVal: ON    No waypoints ready to run"


def test_build_status_line_idle_shows_non_zero_cost() -> None:
    line = build_status_line(
        host_label="HostVal: OFF (LLM-as-judge)",
        message="Paused. Press 'r' to continue",
        cost=0.5,
    )
    assert (
        line == "HostVal: OFF (LLM-as-judge)    $0.50    Paused. Press 'r' to continue"
    )
