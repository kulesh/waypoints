"""Type definitions for the orchestration layer.

This module defines data classes and type aliases used by the JourneyCoordinator
to communicate with UI layers and external callers.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from waypoints.fly.intervention import Intervention
    from waypoints.models.waypoint import Waypoint


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
