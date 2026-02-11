"""Timer lifecycle helpers for Fly screen session state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from textual.timer import Timer

from .fly_session import FlySession


@dataclass
class BudgetWaitTickDecision:
    """Decision output for a budget-wait timer tick."""

    should_refresh_status: bool
    resume_waypoint_id: str | None = None


def clear_budget_wait(session: FlySession) -> None:
    """Stop budget wait timer and clear resume metadata."""
    session.budget_resume_at = None
    session.budget_resume_waypoint_id = None
    if session.budget_wait_timer is not None:
        session.budget_wait_timer.stop()
    session.budget_wait_timer = None


def activate_budget_wait(
    session: FlySession,
    *,
    waypoint_id: str | None,
    resume_at: datetime | None,
    start_timer: Callable[[], Timer],
) -> int | None:
    """Configure budget wait timer and return remaining seconds if countdown exists."""
    clear_budget_wait(session)
    session.budget_resume_waypoint_id = waypoint_id
    session.budget_resume_at = resume_at
    if resume_at is None:
        return None

    session.budget_wait_timer = start_timer()
    return max(0, int((resume_at - datetime.now(UTC)).total_seconds()))


def evaluate_budget_wait_tick(session: FlySession) -> BudgetWaitTickDecision:
    """Evaluate countdown state and decide whether to resume execution."""
    if session.budget_resume_at is None:
        clear_budget_wait(session)
        return BudgetWaitTickDecision(should_refresh_status=False)

    remaining_secs = int((session.budget_resume_at - datetime.now(UTC)).total_seconds())
    if remaining_secs > 0:
        return BudgetWaitTickDecision(should_refresh_status=True)

    resume_waypoint_id = session.budget_resume_waypoint_id
    clear_budget_wait(session)
    return BudgetWaitTickDecision(
        should_refresh_status=False,
        resume_waypoint_id=resume_waypoint_id,
    )


def transition_execution_timers(
    session: FlySession,
    *,
    state: str,
    start_ticker: Callable[[], Timer],
) -> None:
    """Apply timer transitions for execution state changes."""
    if state == "running":
        if session.ticker_timer is not None:
            session.ticker_timer.stop()
        session.execution_start = datetime.now(UTC)
        session.ticker_timer = start_ticker()
        return

    if state == "paused":
        if session.execution_start is not None:
            elapsed = (datetime.now(UTC) - session.execution_start).total_seconds()
            session.elapsed_before_pause += elapsed
        if session.ticker_timer is not None:
            session.ticker_timer.stop()
            session.ticker_timer = None
        return

    if state in {"done", "idle"}:
        if session.ticker_timer is not None:
            session.ticker_timer.stop()
            session.ticker_timer = None
        session.elapsed_before_pause = 0.0
        session.execution_start = None
        clear_budget_wait(session)


def elapsed_seconds(session: FlySession) -> int | None:
    """Get current elapsed seconds for status ticker updates."""
    if session.execution_start is None:
        return None
    current_elapsed = (datetime.now(UTC) - session.execution_start).total_seconds()
    return int(session.elapsed_before_pause + current_elapsed)
