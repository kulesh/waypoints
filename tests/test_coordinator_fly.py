"""Tests for fly-specific coordinator capability helpers."""

from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionType,
)
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.coordinator_fly import (
    build_completion_status,
    build_intervention_resolution,
    build_next_action_after_success,
    prepare_waypoint_for_rerun,
    select_next_waypoint_candidate,
)


def test_build_completion_status_handles_missing_plan() -> None:
    status = build_completion_status(None)

    assert status.total == 0
    assert status.complete == 0
    assert status.pending == 0
    assert status.failed == 0
    assert status.blocked == 0
    assert status.in_progress == 0
    assert status.all_complete is True


def test_build_completion_status_counts_skipped_as_complete() -> None:
    plan = FlightPlan()
    plan.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Done",
            objective="Complete",
            status=WaypointStatus.COMPLETE,
        )
    )
    plan.add_waypoint(
        Waypoint(
            id="WP-002",
            title="Skipped",
            objective="Skipped",
            status=WaypointStatus.SKIPPED,
        )
    )

    status = build_completion_status(plan)

    assert status.total == 2
    assert status.complete == 2
    assert status.all_complete is True


def test_build_completion_status_splits_pending_failed_blocked_and_in_progress() -> (
    None
):
    plan = FlightPlan()
    plan.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Failed dependency",
            objective="Failed",
            status=WaypointStatus.FAILED,
        )
    )
    plan.add_waypoint(
        Waypoint(
            id="WP-002",
            title="Blocked by failure",
            objective="Blocked",
            status=WaypointStatus.PENDING,
            dependencies=["WP-001"],
        )
    )
    plan.add_waypoint(
        Waypoint(
            id="WP-003",
            title="Pending",
            objective="Ready soon",
            status=WaypointStatus.PENDING,
        )
    )
    plan.add_waypoint(
        Waypoint(
            id="WP-004",
            title="Running",
            objective="In progress",
            status=WaypointStatus.IN_PROGRESS,
        )
    )

    status = build_completion_status(plan)

    assert status.total == 4
    assert status.complete == 0
    assert status.failed == 1
    assert status.blocked == 1
    assert status.pending == 1
    assert status.in_progress == 1
    assert status.all_complete is False


def test_select_next_candidate_returns_first_pending_with_met_deps() -> None:
    plan = FlightPlan()
    done = Waypoint(
        id="WP-001",
        title="Done",
        objective="Done",
        status=WaypointStatus.COMPLETE,
    )
    candidate = Waypoint(
        id="WP-002",
        title="Ready",
        objective="Ready",
        status=WaypointStatus.PENDING,
        dependencies=["WP-001"],
    )
    blocked = Waypoint(
        id="WP-003",
        title="Blocked",
        objective="Blocked",
        status=WaypointStatus.PENDING,
        dependencies=["WP-999"],
    )
    plan.add_waypoint(done)
    plan.add_waypoint(candidate)
    plan.add_waypoint(blocked)

    waypoint = select_next_waypoint_candidate(plan)

    assert waypoint is not None
    assert waypoint.id == "WP-002"


def test_select_next_candidate_resumes_in_progress_or_failed() -> None:
    plan = FlightPlan()
    in_progress = Waypoint(
        id="WP-010",
        title="Running",
        objective="In progress",
        status=WaypointStatus.IN_PROGRESS,
    )
    failed = Waypoint(
        id="WP-011",
        title="Failed",
        objective="Failed",
        status=WaypointStatus.FAILED,
    )
    pending = Waypoint(
        id="WP-012",
        title="Pending",
        objective="Pending",
        status=WaypointStatus.PENDING,
    )
    plan.add_waypoint(in_progress)
    plan.add_waypoint(failed)
    plan.add_waypoint(pending)

    waypoint = select_next_waypoint_candidate(plan, include_failed=True)

    assert waypoint is not None
    assert waypoint.id == "WP-010"


def test_prepare_waypoint_for_rerun_resets_complete_waypoint() -> None:
    waypoint = Waypoint(
        id="WP-100",
        title="Completed",
        objective="Done",
        status=WaypointStatus.COMPLETE,
    )

    changed = prepare_waypoint_for_rerun(waypoint)

    assert changed is True
    assert waypoint.status == WaypointStatus.PENDING
    assert waypoint.completed_at is None


def test_prepare_waypoint_for_rerun_ignores_pending_waypoint() -> None:
    waypoint = Waypoint(
        id="WP-101",
        title="Pending",
        objective="Todo",
        status=WaypointStatus.PENDING,
    )

    changed = prepare_waypoint_for_rerun(waypoint)

    assert changed is False
    assert waypoint.status == WaypointStatus.PENDING


def test_select_next_waypoint_candidate_handles_epics_readiness() -> None:
    plan = FlightPlan()
    epic = Waypoint(
        id="WP-100",
        title="Parent Epic",
        objective="Parent",
        status=WaypointStatus.PENDING,
    )
    child = Waypoint(
        id="WP-101",
        title="Child",
        objective="Child",
        status=WaypointStatus.PENDING,
        parent_id="WP-100",
    )
    plan.add_waypoint(epic)
    plan.add_waypoint(child)

    first = select_next_waypoint_candidate(plan)
    assert first is not None
    assert first.id == "WP-101"

    child.status = WaypointStatus.COMPLETE
    second = select_next_waypoint_candidate(plan)
    assert second is not None
    assert second.id == "WP-100"


def test_build_next_action_after_success_returns_continue_when_ready() -> None:
    plan = FlightPlan()
    plan.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Done",
            objective="Done",
            status=WaypointStatus.COMPLETE,
        )
    )
    plan.add_waypoint(
        Waypoint(
            id="WP-002",
            title="Ready",
            objective="Ready",
            status=WaypointStatus.PENDING,
            dependencies=["WP-001"],
        )
    )

    action = build_next_action_after_success(plan)

    assert action.action == "continue"
    assert action.waypoint is not None
    assert action.waypoint.id == "WP-002"


def test_build_next_action_after_success_returns_complete_when_all_done() -> None:
    plan = FlightPlan()
    plan.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Done",
            objective="Done",
            status=WaypointStatus.COMPLETE,
        )
    )

    action = build_next_action_after_success(plan)

    assert action.action == "complete"
    assert action.message == "All waypoints complete!"


def test_build_next_action_after_success_returns_pause_for_blocked() -> None:
    plan = FlightPlan()
    plan.add_waypoint(
        Waypoint(
            id="WP-001",
            title="Failed",
            objective="Failed",
            status=WaypointStatus.FAILED,
        )
    )
    plan.add_waypoint(
        Waypoint(
            id="WP-002",
            title="Blocked",
            objective="Blocked",
            status=WaypointStatus.PENDING,
            dependencies=["WP-001"],
        )
    )

    action = build_next_action_after_success(plan)

    assert action.action == "pause"
    assert action.message == "1 waypoint(s) blocked by failures"


def test_build_intervention_resolution_retry_requests_continue() -> None:
    plan = FlightPlan()
    waypoint = Waypoint(
        id="WP-001",
        title="Retry target",
        objective="Retry",
        status=WaypointStatus.FAILED,
    )
    plan.add_waypoint(waypoint)
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
    )

    new_status, action = build_intervention_resolution(
        flight_plan=plan,
        intervention=intervention,
        action=InterventionAction.RETRY,
        additional_iterations=7,
    )

    assert new_status == WaypointStatus.IN_PROGRESS
    assert action.action == "continue"
    assert action.waypoint == waypoint
    assert action.message == "Retrying with 7 more iterations"


def test_build_intervention_resolution_skip_computes_next_action_without_mutation() -> (
    None
):
    plan = FlightPlan()
    skipped = Waypoint(
        id="WP-001",
        title="Skip target",
        objective="Skip",
        status=WaypointStatus.FAILED,
    )
    ready = Waypoint(
        id="WP-002",
        title="Ready next",
        objective="Continue",
        status=WaypointStatus.PENDING,
    )
    plan.add_waypoint(skipped)
    plan.add_waypoint(ready)
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=skipped,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
    )

    new_status, action = build_intervention_resolution(
        flight_plan=plan,
        intervention=intervention,
        action=InterventionAction.SKIP,
    )

    assert skipped.status == WaypointStatus.FAILED
    assert new_status == WaypointStatus.SKIPPED
    assert action.action == "continue"
    assert action.waypoint is not None
    assert action.waypoint.id == "WP-002"


def test_build_intervention_resolution_rollback_requests_pause() -> None:
    plan = FlightPlan()
    waypoint = Waypoint(
        id="WP-001",
        title="Rollback target",
        objective="Rollback",
        status=WaypointStatus.FAILED,
    )
    plan.add_waypoint(waypoint)
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
    )

    new_status, action = build_intervention_resolution(
        flight_plan=plan,
        intervention=intervention,
        action=InterventionAction.ROLLBACK,
        rollback_tag="waypoint/WP-000",
    )

    assert new_status is None
    assert action.action == "pause"
    assert action.message == "Rollback requested for waypoint/WP-000"


def test_build_intervention_resolution_abort_marks_failed_and_aborts() -> None:
    plan = FlightPlan()
    waypoint = Waypoint(
        id="WP-001",
        title="Abort target",
        objective="Abort",
        status=WaypointStatus.FAILED,
    )
    plan.add_waypoint(waypoint)
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
    )

    new_status, action = build_intervention_resolution(
        flight_plan=plan,
        intervention=intervention,
        action=InterventionAction.ABORT,
    )

    assert new_status == WaypointStatus.FAILED
    assert action.action == "abort"
    assert action.message == "Execution aborted"


def test_build_intervention_resolution_edit_returns_pause_without_status_change() -> (
    None
):
    plan = FlightPlan()
    waypoint = Waypoint(
        id="WP-001",
        title="Edit target",
        objective="Edit",
        status=WaypointStatus.FAILED,
    )
    plan.add_waypoint(waypoint)
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
    )

    new_status, action = build_intervention_resolution(
        flight_plan=plan,
        intervention=intervention,
        action=InterventionAction.EDIT,
    )

    assert new_status is None
    assert action.action == "pause"
    assert action.message == "Edit waypoint and retry"


def test_build_intervention_resolution_wait_sets_pending_and_pauses() -> None:
    plan = FlightPlan()
    waypoint = Waypoint(
        id="WP-001",
        title="Budget wait target",
        objective="Wait",
        status=WaypointStatus.FAILED,
    )
    plan.add_waypoint(waypoint)
    intervention = Intervention(
        type=InterventionType.BUDGET_EXCEEDED,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Budget",
    )

    new_status, action = build_intervention_resolution(
        flight_plan=plan,
        intervention=intervention,
        action=InterventionAction.WAIT,
    )

    assert new_status == WaypointStatus.PENDING
    assert action.action == "pause"
    assert action.message == "Paused waiting for budget reset"
