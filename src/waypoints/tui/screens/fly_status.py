"""Status message helpers for Fly screen."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from waypoints.models.waypoint import Waypoint
from waypoints.orchestration.fly_presenter import build_state_message


def format_countdown(total_seconds: int) -> str:
    """Format countdown as HH:MM:SS or MM:SS."""
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def derive_state_message(
    *,
    state: str,
    current_waypoint: Waypoint | None,
    get_completion_status: Callable[[], tuple[bool, int, int, int]],
    budget_resume_waypoint_id: str | None,
    budget_resume_at: datetime | None,
) -> str:
    """Build status-bar message with budget-wait awareness."""
    if state == "paused" and budget_resume_waypoint_id:
        if budget_resume_at:
            remaining_secs = max(
                0,
                int((budget_resume_at - datetime.now(UTC)).total_seconds()),
            )
            return (
                f"Budget pause on {budget_resume_waypoint_id}. "
                f"Auto-resume in {format_countdown(remaining_secs)}"
            )
        return (
            f"Budget pause on {budget_resume_waypoint_id}. "
            "Press 'r' after budget resets."
        )

    return build_state_message(
        state=state,
        current_waypoint=current_waypoint,
        get_completion_status=get_completion_status,
    )
