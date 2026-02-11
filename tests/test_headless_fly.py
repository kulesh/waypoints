"""Unit tests for shared headless fly execution helper."""

from __future__ import annotations

import asyncio

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import (
    Intervention,
    InterventionNeededError,
    InterventionType,
)
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.headless_fly import execute_waypoint_with_coordinator
from waypoints.orchestration.types import NextAction


def _waypoint() -> Waypoint:
    return Waypoint(id="WP-001", title="One", objective="Do one")


def _intervention(waypoint: Waypoint) -> Intervention:
    return Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=1,
        max_iterations=3,
        error_summary="broken",
    )


class _FakeCoordinator:
    def __init__(
        self,
        *,
        result: ExecutionResult | None = None,
        error: Exception | None = None,
        next_action: NextAction | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self._next_action = next_action or NextAction(action="pause")
        self.marked: list[tuple[Waypoint, WaypointStatus]] = []

    async def execute_waypoint(
        self,
        waypoint: Waypoint,
        *,
        max_iterations: int,
        host_validations_enabled: bool,
    ) -> ExecutionResult:
        del waypoint, max_iterations, host_validations_enabled
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    def handle_execution_result(
        self,
        waypoint: Waypoint,
        result: ExecutionResult,
    ) -> NextAction:
        del waypoint, result
        return self._next_action

    def mark_waypoint_status(self, waypoint: Waypoint, status: WaypointStatus) -> None:
        self.marked.append((waypoint, status))


def test_execute_waypoint_with_coordinator_success() -> None:
    waypoint = _waypoint()
    coordinator = _FakeCoordinator(
        result=ExecutionResult.SUCCESS,
        next_action=NextAction(action="complete", waypoint=waypoint),
    )

    outcome = asyncio.run(
        execute_waypoint_with_coordinator(
            coordinator,
            waypoint,
            max_iterations=10,
            host_validations_enabled=True,
        )
    )

    assert outcome.kind == "success"
    assert outcome.result == ExecutionResult.SUCCESS
    assert outcome.next_action is not None
    assert outcome.next_action.action == "complete"
    assert coordinator.marked == []


def test_execute_waypoint_with_coordinator_failed_result() -> None:
    waypoint = _waypoint()
    coordinator = _FakeCoordinator(
        result=ExecutionResult.FAILED,
        next_action=NextAction(action="intervention", waypoint=waypoint),
    )

    outcome = asyncio.run(
        execute_waypoint_with_coordinator(
            coordinator,
            waypoint,
            max_iterations=10,
            host_validations_enabled=True,
        )
    )

    assert outcome.kind == "failed"
    assert outcome.result == ExecutionResult.FAILED
    assert outcome.next_action is not None
    assert outcome.next_action.action == "intervention"
    assert coordinator.marked == []


def test_execute_waypoint_with_coordinator_intervention() -> None:
    waypoint = _waypoint()
    coordinator = _FakeCoordinator(
        error=InterventionNeededError(_intervention(waypoint)),
    )

    outcome = asyncio.run(
        execute_waypoint_with_coordinator(
            coordinator,
            waypoint,
            max_iterations=10,
            host_validations_enabled=True,
        )
    )

    assert outcome.kind == "intervention"
    assert outcome.intervention is not None
    assert outcome.intervention.error_summary == "broken"
    assert coordinator.marked == [(waypoint, WaypointStatus.FAILED)]


def test_execute_waypoint_with_coordinator_unexpected_error() -> None:
    waypoint = _waypoint()
    coordinator = _FakeCoordinator(error=RuntimeError("boom"))

    outcome = asyncio.run(
        execute_waypoint_with_coordinator(
            coordinator,
            waypoint,
            max_iterations=10,
            host_validations_enabled=True,
        )
    )

    assert outcome.kind == "error"
    assert isinstance(outcome.error, RuntimeError)
    assert coordinator.marked == [(waypoint, WaypointStatus.FAILED)]
