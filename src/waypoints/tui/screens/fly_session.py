"""Session state for Fly screen execution and timers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from textual.timer import Timer


@dataclass
class FlySession:
    """Mutable UI session state for fly execution lifecycle."""

    additional_iterations: int = 0
    execution_start: datetime | None = None
    elapsed_before_pause: float = 0.0
    ticker_timer: Timer | None = None
    budget_wait_timer: Timer | None = None
    budget_resume_at: datetime | None = None
    budget_resume_waypoint_id: str | None = None
