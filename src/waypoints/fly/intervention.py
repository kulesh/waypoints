"""Intervention protocol for handling execution failures.

This module provides structured intervention types and actions when
waypoint execution fails or needs human input.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from waypoints.models.waypoint import Waypoint


class InterventionType(Enum):
    """Types of situations requiring human intervention."""

    ITERATION_LIMIT = "iteration_limit"  # Hit MAX_ITERATIONS
    TEST_FAILURE = "test_failure"  # Tests won't pass
    LINT_ERROR = "lint_error"  # Linting blocked
    TYPE_ERROR = "type_error"  # Type checker fails
    PARSE_ERROR = "parse_error"  # Invalid output from LLM
    USER_REQUESTED = "user_requested"  # User pressed interrupt key
    EXECUTION_ERROR = "execution_error"  # General execution failure
    RATE_LIMITED = "rate_limited"  # Claude API rate limit hit
    API_UNAVAILABLE = "api_unavailable"  # Service temporarily down
    BUDGET_EXCEEDED = "budget_exceeded"  # Daily/monthly budget hit


class InterventionAction(Enum):
    """Actions the user can take when intervention is needed."""

    RETRY = "retry"  # Try waypoint again (maybe with more iterations)
    SKIP = "skip"  # Mark waypoint skipped, continue to next
    EDIT = "edit"  # Open waypoint editor, then retry
    ROLLBACK = "rollback"  # Rollback to last safe tag
    ABORT = "abort"  # Stop execution entirely


# Suggested actions based on intervention type
SUGGESTED_ACTIONS: dict[InterventionType, InterventionAction] = {
    InterventionType.ITERATION_LIMIT: InterventionAction.RETRY,
    InterventionType.TEST_FAILURE: InterventionAction.EDIT,
    InterventionType.LINT_ERROR: InterventionAction.RETRY,
    InterventionType.TYPE_ERROR: InterventionAction.RETRY,
    InterventionType.PARSE_ERROR: InterventionAction.RETRY,
    InterventionType.USER_REQUESTED: InterventionAction.ABORT,
    InterventionType.EXECUTION_ERROR: InterventionAction.RETRY,
    InterventionType.RATE_LIMITED: InterventionAction.RETRY,
    InterventionType.API_UNAVAILABLE: InterventionAction.RETRY,
    InterventionType.BUDGET_EXCEEDED: InterventionAction.ABORT,
}


@dataclass
class Intervention:
    """Captures context when intervention is needed.

    This dataclass holds all the information needed for the user
    to make an informed decision about how to proceed.
    """

    type: InterventionType
    waypoint: Waypoint
    iteration: int
    max_iterations: int
    error_summary: str
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def suggested_action(self) -> InterventionAction:
        """Get the suggested action for this intervention type."""
        return SUGGESTED_ACTIONS.get(self.type, InterventionAction.RETRY)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/serialization."""
        return {
            "type": self.type.value,
            "waypoint_id": self.waypoint.id,
            "waypoint_title": self.waypoint.title,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "error_summary": self.error_summary,
            "suggested_action": self.suggested_action.value,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class InterventionResult:
    """Result of user's intervention decision."""

    action: InterventionAction
    modified_waypoint: Waypoint | None = None  # If EDIT was chosen
    additional_iterations: int = 5  # If RETRY was chosen
    rollback_tag: str | None = None  # If ROLLBACK was chosen

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "action": self.action.value,
            "modified_waypoint_id": (
                self.modified_waypoint.id if self.modified_waypoint else None
            ),
            "additional_iterations": self.additional_iterations,
            "rollback_tag": self.rollback_tag,
        }


class InterventionNeededError(Exception):
    """Exception raised when execution needs human intervention.

    This replaces returning ExecutionResult directly, allowing the
    fly screen to catch and show the intervention modal.
    """

    def __init__(self, intervention: Intervention) -> None:
        self.intervention = intervention
        super().__init__(
            f"Intervention needed: {intervention.type.value} "
            f"at iteration {intervention.iteration}"
        )
