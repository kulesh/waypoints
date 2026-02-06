"""Orchestration layer for Waypoints.

This package contains the JourneyCoordinator which orchestrates
business logic independent of the UI layer.

Usage:
    from waypoints.orchestration import JourneyCoordinator, NextAction

    coordinator = JourneyCoordinator(project)
    next_wp = coordinator.select_next_waypoint()
    result = await coordinator.execute_waypoint(next_wp)
    action = coordinator.handle_execution_result(next_wp, result)
"""

from waypoints.orchestration.coordinator import JourneyCoordinator
from waypoints.orchestration.execution_controller import (
    ExecutionController,
    ExecutionDirective,
)
from waypoints.orchestration.types import (
    ChunkCallback,
    CompletionStatus,
    NextAction,
    ProgressCallback,
    ProgressUpdate,
    TextStream,
)

__all__ = [
    "JourneyCoordinator",
    "ExecutionController",
    "ExecutionDirective",
    "NextAction",
    "CompletionStatus",
    "ProgressCallback",
    "ProgressUpdate",
    "ChunkCallback",
    "TextStream",
]
