"""Type definitions for the orchestration layer.

This module defines data classes and type aliases used by the JourneyCoordinator
to communicate with UI layers and external callers.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from waypoints.fly.intervention import Intervention
    from waypoints.models.flight_plan import FlightPlan
    from waypoints.models.waypoint import Waypoint


# --- Intervention Presentation ---


@dataclass
class InterventionPresentation:
    """How the UI should present an intervention to the user.

    Returned by FlyPhase.classify_intervention() so FlyScreen only
    deals with presentation, not business classification.
    """

    show_modal: bool
    """True: show the intervention modal. False: auto-handle (budget wait)."""

    intervention: "Intervention"
    """The original intervention object."""


# --- Callback Types ---


@dataclass
class ProgressUpdate:
    """Progress update during waypoint execution."""

    waypoint_id: str
    iteration: int
    total_iterations: int
    step: str  # "executing", "streaming", "complete", "error", "finalizing"
    output: str
    criteria_completed: set[int] = field(default_factory=set)


ProgressCallback = Callable[[ProgressUpdate], None]
"""Called during execution to report progress."""

ChunkCallback = Callable[[str], None]
"""Called for each streamed text chunk from LLM."""


# --- Result Types ---


@dataclass
class BudgetWaitDetails:
    """Extracted budget wait information from an intervention.

    Computed by FlyPhase so the UI doesn't need to parse intervention context.
    """

    waypoint_id: str
    """The waypoint that was paused for budget."""

    resume_at: "datetime | None" = None
    """UTC timestamp when budget resets, or None if unknown."""

    configured_budget_usd: float | None = None
    """The configured budget limit."""

    current_cost_usd: float | None = None
    """How much has been spent so far."""


@dataclass
class RollbackResult:
    """Result of a git rollback operation."""

    success: bool
    message: str
    resolved_ref: str | None = None
    flight_plan: "FlightPlan | None" = None
    """Reloaded flight plan after rollback, or None on failure."""


@dataclass
class CommitResult:
    """Result of committing a waypoint to git."""

    committed: bool
    message: str
    commit_hash: str | None = None
    tag_name: str | None = None
    initialized_repo: bool = False


@dataclass
class NextAction:
    """What should happen next after an operation.

    Returned by coordinator methods to tell the UI what to do next.
    """

    action: Literal[
        "continue",  # Execute next waypoint
        "pause",  # Stop and wait for user
        "intervention",  # Show intervention modal
        "complete",  # All waypoints done
        "abort",  # Stop execution entirely
    ]
    waypoint: "Waypoint | None" = None
    intervention: "Intervention | None" = None
    message: str | None = None
    commit_result: CommitResult | None = None


@dataclass
class CompletionStatus:
    """Summary of waypoint completion state."""

    total: int
    complete: int
    pending: int
    failed: int
    blocked: int
    in_progress: int = 0

    @property
    def all_complete(self) -> bool:
        """Check if all waypoints are complete."""
        return self.complete == self.total

    @property
    def has_failed(self) -> bool:
        """Check if any waypoints have failed."""
        return self.failed > 0

    @property
    def has_blocked(self) -> bool:
        """Check if any waypoints are blocked."""
        return self.blocked > 0


# --- Async Iterator Type Aliases ---

TextStream = AsyncIterator[str]
"""Async iterator yielding text chunks from LLM."""
