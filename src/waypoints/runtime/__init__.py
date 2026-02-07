"""Runtime primitives shared across orchestration and execution layers."""

from waypoints.runtime.command_runner import (
    CommandEvent,
    CommandResult,
    get_command_runner,
)
from waypoints.runtime.timeout_policy import TimeoutDomain, get_timeout_policy_registry

__all__ = [
    "CommandEvent",
    "CommandResult",
    "TimeoutDomain",
    "get_command_runner",
    "get_timeout_policy_registry",
]
