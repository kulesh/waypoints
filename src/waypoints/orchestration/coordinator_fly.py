"""Fly-phase orchestration helpers for coordinator decomposition."""

from __future__ import annotations

from waypoints.fly.intervention import Intervention, InterventionAction
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.types import CompletionStatus, NextAction


def prepare_waypoint_for_rerun(waypoint: Waypoint) -> bool:
    """Reset a completed/failed waypoint back to PENDING for re-execution."""
    if waypoint.status not in (WaypointStatus.COMPLETE, WaypointStatus.FAILED):
        return False
    waypoint.status = WaypointStatus.PENDING
    waypoint.completed_at = None
    return True


def build_completion_status(flight_plan: FlightPlan | None) -> CompletionStatus:
    """Compute completion summary for a flight plan."""
    if flight_plan is None:
        return CompletionStatus(total=0, complete=0, pending=0, failed=0, blocked=0)

    total = 0
    complete = 0
    pending = 0
    failed = 0
    blocked = 0
    in_progress = 0

    for waypoint in flight_plan.waypoints:
        total += 1

        if waypoint.status == WaypointStatus.COMPLETE:
            complete += 1
        elif waypoint.status == WaypointStatus.SKIPPED:
            complete += 1  # Count skipped as done.
        elif waypoint.status == WaypointStatus.FAILED:
            failed += 1
        elif waypoint.status == WaypointStatus.IN_PROGRESS:
            in_progress += 1
        elif waypoint.status == WaypointStatus.PENDING:
            is_blocked = False
            for dep_id in waypoint.dependencies:
                dependency = flight_plan.get_waypoint(dep_id)
                if (
                    dependency is not None
                    and dependency.status == WaypointStatus.FAILED
                ):
                    is_blocked = True
                    break
            if is_blocked:
                blocked += 1
            else:
                pending += 1

    return CompletionStatus(
        total=total,
        complete=complete,
        pending=pending,
        failed=failed,
        blocked=blocked,
        in_progress=in_progress,
    )


def _dependencies_met(flight_plan: FlightPlan, waypoint: Waypoint) -> bool:
    """Check if all waypoint dependencies are complete or skipped."""
    for dep_id in waypoint.dependencies:
        dependency = flight_plan.get_waypoint(dep_id)
        if dependency is None:
            return False
        if dependency.status not in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED):
            return False
    return True


def select_next_waypoint_candidate(
    flight_plan: FlightPlan | None, include_failed: bool = False
) -> Waypoint | None:
    """Find next executable waypoint without mutating coordinator state."""
    if flight_plan is None:
        return None

    if include_failed:
        for waypoint in flight_plan.waypoints:
            if waypoint.status in (WaypointStatus.IN_PROGRESS, WaypointStatus.FAILED):
                return waypoint

    for waypoint in flight_plan.waypoints:
        if waypoint.status != WaypointStatus.PENDING:
            continue

        if flight_plan.is_epic(waypoint.id):
            children = flight_plan.get_children(waypoint.id)
            if any(
                child.status not in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED)
                for child in children
            ):
                continue

        if not _dependencies_met(flight_plan, waypoint):
            continue

        return waypoint

    return None


def build_next_action_after_success(flight_plan: FlightPlan | None) -> NextAction:
    """Determine next action after a successful waypoint execution."""
    next_waypoint = select_next_waypoint_candidate(flight_plan)
    if next_waypoint is not None:
        return NextAction(action="continue", waypoint=next_waypoint)

    status = build_completion_status(flight_plan)
    if status.all_complete:
        return NextAction(action="complete", message="All waypoints complete!")

    if status.blocked > 0:
        return NextAction(
            action="pause",
            message=f"{status.blocked} waypoint(s) blocked by failures",
        )

    if status.failed > 0:
        return NextAction(
            action="pause",
            message=f"{status.failed} waypoint(s) failed",
        )

    pending_total = status.pending + status.in_progress
    if pending_total > 0:
        return NextAction(
            action="pause",
            message=f"{pending_total} waypoint(s) waiting",
        )

    return NextAction(action="pause", message="No executable waypoints available")


def build_intervention_resolution(
    *,
    flight_plan: FlightPlan | None,
    intervention: Intervention,
    action: InterventionAction,
    additional_iterations: int = 5,
    rollback_tag: str | None = None,
) -> tuple[WaypointStatus | None, NextAction]:
    """Map an intervention action to a waypoint status change and next action."""
    waypoint = intervention.waypoint

    if action == InterventionAction.RETRY:
        return (
            WaypointStatus.IN_PROGRESS,
            NextAction(
                action="continue",
                waypoint=waypoint,
                message=f"Retrying with {additional_iterations} more iterations",
            ),
        )

    if action == InterventionAction.SKIP:
        original_status = waypoint.status
        waypoint.status = WaypointStatus.SKIPPED
        next_action = build_next_action_after_success(flight_plan)
        waypoint.status = original_status
        return (WaypointStatus.SKIPPED, next_action)

    if action == InterventionAction.ROLLBACK:
        return (
            WaypointStatus.PENDING,
            NextAction(action="pause", message=f"Rolled back to {rollback_tag}"),
        )

    if action == InterventionAction.ABORT:
        return (
            WaypointStatus.FAILED,
            NextAction(action="abort", message="Execution aborted"),
        )

    if action == InterventionAction.WAIT:
        return (
            WaypointStatus.PENDING,
            NextAction(action="pause", message="Paused waiting for budget reset"),
        )

    if action == InterventionAction.EDIT:
        return (
            None,
            NextAction(action="pause", message="Edit waypoint and retry"),
        )

    return (None, NextAction(action="pause"))
