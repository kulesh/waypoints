"""Execution state model for the FLY phase."""

from __future__ import annotations

from enum import Enum


class ExecutionState(Enum):
    """State of waypoint execution."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSE_PENDING = "pause_pending"  # Pause requested, finishing current waypoint
    PAUSED = "paused"
    DONE = "done"
    INTERVENTION = "intervention"
