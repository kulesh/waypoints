"""Shared headless FLY execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import Intervention, InterventionNeededError
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.types import NextAction

if TYPE_CHECKING:
    from waypoints.orchestration.coordinator import JourneyCoordinator


@dataclass
class WaypointExecutionOutcome:
    """Result of executing a single waypoint through the coordinator."""

    kind: Literal["success", "failed", "intervention", "error"]
    waypoint: Waypoint
    result: ExecutionResult | None = None
    next_action: NextAction | None = None
    intervention: Intervention | None = None
    error: Exception | None = None


async def execute_waypoint_with_coordinator(
    coordinator: "JourneyCoordinator",
    waypoint: Waypoint,
    *,
    max_iterations: int,
    host_validations_enabled: bool,
) -> WaypointExecutionOutcome:
    """Execute one waypoint and normalize outcomes for headless callers."""
    try:
        result = await coordinator.execute_waypoint(
            waypoint,
            max_iterations=max_iterations,
            host_validations_enabled=host_validations_enabled,
        )
        next_action = coordinator.handle_execution_result(waypoint, result)
        if result == ExecutionResult.SUCCESS:
            return WaypointExecutionOutcome(
                kind="success",
                waypoint=waypoint,
                result=result,
                next_action=next_action,
            )
        return WaypointExecutionOutcome(
            kind="failed",
            waypoint=waypoint,
            result=result,
            next_action=next_action,
        )
    except InterventionNeededError as err:
        coordinator.mark_waypoint_status(waypoint, WaypointStatus.FAILED)
        return WaypointExecutionOutcome(
            kind="intervention",
            waypoint=waypoint,
            intervention=err.intervention,
        )
    except Exception as err:  # noqa: BLE001
        coordinator.mark_waypoint_status(waypoint, WaypointStatus.FAILED)
        return WaypointExecutionOutcome(
            kind="error",
            waypoint=waypoint,
            error=err,
        )
