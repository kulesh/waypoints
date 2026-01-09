"""Journey state machine for tracking project phases.

This module provides explicit state tracking with transition validation
for the Waypoints journey from idea to working software.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class JourneyState(Enum):
    """All possible states in the Waypoints journey."""

    # SPARK phase - Initial idea entry
    SPARK_IDLE = "spark:idle"
    SPARK_ENTERING = "spark:entering"

    # SHAPE phase - Idea refinement
    SHAPE_QA = "shape:qa"
    SHAPE_BRIEF_GENERATING = "shape:brief:generating"
    SHAPE_BRIEF_REVIEW = "shape:brief:review"
    SHAPE_SPEC_GENERATING = "shape:spec:generating"
    SHAPE_SPEC_REVIEW = "shape:spec:review"

    # CHART phase - Flight plan creation
    CHART_GENERATING = "chart:generating"
    CHART_REVIEW = "chart:review"

    # FLY phase - Waypoint execution
    FLY_READY = "fly:ready"
    FLY_EXECUTING = "fly:executing"
    FLY_PAUSED = "fly:paused"
    FLY_INTERVENTION = "fly:intervention"

    # LAND phase - Completion
    LANDED = "landed"


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: JourneyState, target: JourneyState) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transition from {current.value} to {target.value}"
        )


# Valid state transitions table
VALID_TRANSITIONS: dict[JourneyState, set[JourneyState]] = {
    # SPARK phase
    JourneyState.SPARK_IDLE: {JourneyState.SPARK_ENTERING},
    JourneyState.SPARK_ENTERING: {JourneyState.SHAPE_QA},
    # SHAPE phase
    JourneyState.SHAPE_QA: {JourneyState.SHAPE_BRIEF_GENERATING},
    JourneyState.SHAPE_BRIEF_GENERATING: {JourneyState.SHAPE_BRIEF_REVIEW},
    JourneyState.SHAPE_BRIEF_REVIEW: {
        JourneyState.SHAPE_BRIEF_GENERATING,  # Regenerate
        JourneyState.SHAPE_SPEC_GENERATING,
    },
    JourneyState.SHAPE_SPEC_GENERATING: {JourneyState.SHAPE_SPEC_REVIEW},
    JourneyState.SHAPE_SPEC_REVIEW: {
        JourneyState.SHAPE_SPEC_GENERATING,  # Regenerate
        JourneyState.CHART_GENERATING,
    },
    # CHART phase
    JourneyState.CHART_GENERATING: {JourneyState.CHART_REVIEW},
    JourneyState.CHART_REVIEW: {
        JourneyState.CHART_GENERATING,  # Regenerate
        JourneyState.FLY_READY,
    },
    # FLY phase
    JourneyState.FLY_READY: {JourneyState.FLY_EXECUTING},
    JourneyState.FLY_EXECUTING: {
        JourneyState.FLY_PAUSED,
        JourneyState.FLY_INTERVENTION,
        JourneyState.LANDED,
    },
    JourneyState.FLY_PAUSED: {
        JourneyState.FLY_EXECUTING,  # Resume
        JourneyState.FLY_READY,  # Back to ready
    },
    JourneyState.FLY_INTERVENTION: {
        JourneyState.FLY_EXECUTING,  # Retry
        JourneyState.FLY_PAUSED,  # Skip waypoint
        JourneyState.CHART_REVIEW,  # Edit plan
    },
    # LAND phase - terminal
    JourneyState.LANDED: set(),
}


# Mapping from JourneyState to screen phase name
STATE_TO_PHASE: dict[JourneyState, str] = {
    JourneyState.SPARK_IDLE: "ideation",
    JourneyState.SPARK_ENTERING: "ideation",
    JourneyState.SHAPE_QA: "ideation-qa",
    JourneyState.SHAPE_BRIEF_GENERATING: "idea-brief",
    JourneyState.SHAPE_BRIEF_REVIEW: "idea-brief",
    JourneyState.SHAPE_SPEC_GENERATING: "product-spec",
    JourneyState.SHAPE_SPEC_REVIEW: "product-spec",
    JourneyState.CHART_GENERATING: "chart",
    JourneyState.CHART_REVIEW: "chart",
    JourneyState.FLY_READY: "fly",
    JourneyState.FLY_EXECUTING: "fly",
    JourneyState.FLY_PAUSED: "fly",
    JourneyState.FLY_INTERVENTION: "fly",
    JourneyState.LANDED: "fly",
}


# Mapping from screen phase name to entry state
PHASE_TO_STATE: dict[str, JourneyState] = {
    "ideation": JourneyState.SPARK_IDLE,
    "ideation-qa": JourneyState.SHAPE_QA,
    "idea-brief": JourneyState.SHAPE_BRIEF_GENERATING,
    "product-spec": JourneyState.SHAPE_SPEC_GENERATING,
    "chart": JourneyState.CHART_GENERATING,
    "fly": JourneyState.FLY_READY,
}


# States that are safe to resume from after a crash
RECOVERABLE_STATES: set[JourneyState] = {
    JourneyState.SPARK_IDLE,
    JourneyState.SHAPE_QA,
    JourneyState.SHAPE_BRIEF_REVIEW,
    JourneyState.SHAPE_SPEC_REVIEW,
    JourneyState.CHART_REVIEW,
    JourneyState.FLY_READY,
    JourneyState.FLY_PAUSED,
    JourneyState.LANDED,
}


# Mapping from non-recoverable to nearest recoverable state
RECOVERY_MAP: dict[JourneyState, JourneyState] = {
    JourneyState.SPARK_ENTERING: JourneyState.SPARK_IDLE,
    JourneyState.SHAPE_BRIEF_GENERATING: JourneyState.SHAPE_QA,
    JourneyState.SHAPE_SPEC_GENERATING: JourneyState.SHAPE_BRIEF_REVIEW,
    JourneyState.CHART_GENERATING: JourneyState.SHAPE_SPEC_REVIEW,
    JourneyState.FLY_EXECUTING: JourneyState.FLY_READY,
    JourneyState.FLY_INTERVENTION: JourneyState.FLY_READY,
}


@dataclass
class Journey:
    """Tracks the current state of a project's journey.

    The journey maintains the current state, project association,
    and a history of all state transitions for debugging and auditing.
    """

    state: JourneyState
    project_slug: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    state_history: list[dict[str, str]] = field(default_factory=list)

    def can_transition(self, target: JourneyState) -> bool:
        """Check if transition to target state is valid.

        Args:
            target: The state to transition to.

        Returns:
            True if the transition is allowed, False otherwise.
        """
        return target in VALID_TRANSITIONS.get(self.state, set())

    def transition(self, target: JourneyState) -> "Journey":
        """Transition to a new state, returning a new Journey.

        This method is immutable - it returns a new Journey instance
        rather than modifying the current one.

        Args:
            target: The state to transition to.

        Returns:
            A new Journey instance in the target state.

        Raises:
            InvalidTransitionError: If the transition is not valid.
        """
        if not self.can_transition(target):
            raise InvalidTransitionError(self.state, target)

        now = datetime.now(UTC)
        new_history = self.state_history.copy()
        new_history.append(
            {
                "from": self.state.value,
                "to": target.value,
                "at": now.isoformat(),
            }
        )

        return Journey(
            state=target,
            project_slug=self.project_slug,
            updated_at=now,
            state_history=new_history,
        )

    def recover(self) -> "Journey":
        """Recover to nearest safe state if current state is non-recoverable.

        Returns:
            A new Journey in a recoverable state, or self if already recoverable.
        """
        if self.state in RECOVERABLE_STATES:
            return self

        target = RECOVERY_MAP.get(self.state, JourneyState.SPARK_IDLE)
        now = datetime.now(UTC)
        new_history = self.state_history.copy()
        new_history.append(
            {
                "from": self.state.value,
                "to": target.value,
                "at": now.isoformat(),
                "reason": "recovery",
            }
        )

        return Journey(
            state=target,
            project_slug=self.project_slug,
            updated_at=now,
            state_history=new_history,
        )

    @property
    def phase(self) -> str:
        """Get the screen phase name for this state."""
        return STATE_TO_PHASE[self.state]

    @property
    def is_recoverable(self) -> bool:
        """Check if current state is safe to resume from."""
        return self.state in RECOVERABLE_STATES

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "state": self.state.value,
            "project_slug": self.project_slug,
            "updated_at": self.updated_at.isoformat(),
            "state_history": self.state_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Journey":
        """Create Journey from dictionary.

        Args:
            data: Dictionary with state, project_slug, updated_at, state_history.

        Returns:
            A Journey instance.
        """
        return cls(
            state=JourneyState(data["state"]),
            project_slug=data["project_slug"],
            updated_at=datetime.fromisoformat(data["updated_at"]),
            state_history=data.get("state_history", []),
        )

    @classmethod
    def new(cls, project_slug: str) -> "Journey":
        """Create a new journey for a project.

        Args:
            project_slug: The slug of the project.

        Returns:
            A Journey starting at SPARK_IDLE.
        """
        return cls(
            state=JourneyState.SPARK_IDLE,
            project_slug=project_slug,
        )
