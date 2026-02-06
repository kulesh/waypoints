"""Presentation helpers for FLY execution state messaging."""

from __future__ import annotations

from collections.abc import Callable

from waypoints.models.waypoint import Waypoint, WaypointStatus

type CompletionStatusProvider = Callable[[], tuple[bool, int, int, int]]

MAX_WAYPOINT_TITLE_LENGTH = 40


def _truncate_waypoint_title(title: str) -> str:
    """Truncate waypoint title for compact status bar display."""
    if len(title) <= MAX_WAYPOINT_TITLE_LENGTH:
        return title
    return title[:MAX_WAYPOINT_TITLE_LENGTH] + "..."


def build_state_message(
    *,
    state: str,
    current_waypoint: Waypoint | None,
    get_completion_status: CompletionStatusProvider,
) -> str:
    """Build status bar message for the given execution state.

    Args:
        state: Fly execution state value (e.g., ``"idle"``, ``"running"``).
        current_waypoint: Currently selected waypoint, if any.
        get_completion_status: Callable returning completion tuple:
            ``(all_complete, pending_count, failed_count, blocked_count)``.

    Returns:
        User-facing status bar message for the current execution state.
    """
    if state == "idle":
        if current_waypoint is not None:
            title = _truncate_waypoint_title(current_waypoint.title)
            return f"Press 'r' to run {current_waypoint.id}: {title}"
        return "No waypoints ready to run"

    if state == "running":
        return "Executing waypoint..."

    if state == "pause_pending":
        return "Pausing after current waypoint..."

    if state == "paused":
        if current_waypoint is not None:
            if current_waypoint.status == WaypointStatus.FAILED:
                return f"{current_waypoint.id} failed. Press 'r' to continue"
            return f"Paused. Press 'r' to run {current_waypoint.id}"

        _, pending, _, blocked = get_completion_status()
        if blocked > 0:
            return f"Blocked · {blocked} waypoint(s) need failed deps fixed"
        if pending > 0:
            return f"Paused · {pending} waypoint(s) waiting"
        return "Paused. Press 'r' to continue"

    if state == "done":
        all_complete, pending, failed, blocked = get_completion_status()
        if all_complete:
            return "All waypoints complete!"
        if blocked > 0:
            return f"{blocked} waypoint(s) blocked by failures"
        if failed > 0:
            return f"{failed} waypoint(s) failed"
        return f"{pending} waypoint(s) waiting"

    if state == "intervention":
        if current_waypoint is not None:
            return f"Intervention needed for {current_waypoint.id}"
        return "Intervention needed"

    return ""


def build_status_line(
    *,
    host_label: str,
    message: str,
    cost: float,
    elapsed_seconds: int | None = None,
) -> str:
    """Build the status bar line for running and non-running states."""
    if elapsed_seconds is not None:
        minutes, seconds = divmod(elapsed_seconds, 60)
        return f"{host_label}    ⏱ {minutes}:{seconds:02d} | ${cost:.2f}    {message}"

    if cost > 0:
        return f"{host_label}    ${cost:.2f}    {message}"

    return f"{host_label}    {message}"
