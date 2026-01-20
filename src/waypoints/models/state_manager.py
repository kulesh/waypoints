"""Centralized journey state management for a project."""

from __future__ import annotations

from dataclasses import dataclass

from waypoints.models.journey import InvalidTransitionError, Journey, JourneyState
from waypoints.models.project import Project


@dataclass(frozen=True)
class StateGuardError(Exception):
    """Raised when an operation requires a different journey state."""

    current: JourneyState
    allowed: set[JourneyState]

    def __str__(self) -> str:  # pragma: no cover - trivial
        allowed = ", ".join(sorted(state.value for state in self.allowed))
        return f"Invalid state {self.current.value}; expected one of: {allowed}"


class JourneyStateManager:
    """Single gateway for journey transition and recovery logic."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def _ensure_journey(self) -> Journey:
        if self.project.journey is None:
            self.project.journey = Journey.new(self.project.slug)
            self.project.save()
        return self.project.journey

    def current(self) -> JourneyState:
        """Return the current journey state."""
        return self._ensure_journey().state

    def phase(self) -> str:
        """Return the current phase name."""
        return self._ensure_journey().phase

    def recover(self) -> Journey:
        """Recover to a safe state if needed and persist any change."""
        journey = self._ensure_journey()
        recovered = journey.recover()
        if recovered != journey:
            self.project.journey = recovered
            self.project.save()
            return recovered
        return journey

    def is_transition_allowed(self, target: JourneyState) -> bool:
        """Check if a transition is allowed from current state."""
        return self._ensure_journey().can_transition(target)

    def assert_can_transition(self, target: JourneyState) -> None:
        """Raise if transition to target is not allowed."""
        journey = self._ensure_journey()
        if not journey.can_transition(target):
            raise InvalidTransitionError(journey.state, target)

    def transition(self, target: JourneyState, reason: str | None = None) -> Journey:
        """Transition to target state and persist.

        Idempotent if already in the target state.
        """
        journey = self._ensure_journey()
        if journey.state == target:
            return journey
        self.project.transition_journey(target, reason=reason)
        return self._ensure_journey()

    def ensure_state(
        self,
        allowed: set[JourneyState],
        fallback: JourneyState | None = None,
        reason: str | None = None,
    ) -> Journey:
        """Ensure current state is allowed, optionally transitioning to fallback."""
        journey = self._ensure_journey()
        if journey.state in allowed:
            return journey
        if fallback is None:
            raise StateGuardError(journey.state, allowed)
        return self.transition(fallback, reason=reason)
