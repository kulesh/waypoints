"""Utilities for validating journey transitions from screen actions."""

from waypoints.models import Journey, JourneyState


def can_enter_state(journey: Journey | None, target: JourneyState) -> bool:
    """Return True when journey can enter the target state.

    Treat already-being-in-target as valid so screen entry/actions are
    idempotent.
    """
    if journey is None:
        return False
    if journey.state == target:
        return True
    return journey.can_transition(target)
