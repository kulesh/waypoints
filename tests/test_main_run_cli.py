"""Behavior-lock tests for `waypoints run` on-error semantics."""

from __future__ import annotations

import argparse
from pathlib import Path

from waypoints.config.settings import settings
from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import (
    Intervention,
    InterventionNeededError,
    InterventionType,
)
from waypoints.main import cmd_run
from waypoints.models.flight_plan import FlightPlan, FlightPlanWriter
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.types import NextAction


def _create_project_with_plan(tmp_path: Path, count: int) -> tuple[Project, FlightPlan]:
    settings.project_directory = tmp_path
    project = Project.create("Run CLI")
    waypoints = [
        Waypoint(
            id=f"WP-{idx:03d}",
            title=f"Waypoint {idx}",
            objective="Test objective",
            status=WaypointStatus.PENDING,
        )
        for idx in range(1, count + 1)
    ]
    flight_plan = FlightPlan(waypoints=waypoints)
    FlightPlanWriter(project).save(flight_plan)
    return project, flight_plan


def _build_args(project_slug: str, on_error: str) -> argparse.Namespace:
    return argparse.Namespace(
        project=project_slug,
        on_error=on_error,
        max_iterations=3,
    )


def test_cmd_run_abort_stops_on_failed_waypoint(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project, _ = _create_project_with_plan(tmp_path, count=1)
    include_failed_calls: list[bool] = []

    class FakeCoordinator:
        def __init__(self, project, flight_plan) -> None:  # type: ignore[no-untyped-def]
            self.flight_plan = flight_plan
            self._select_calls = 0

        def reset_stale_in_progress(self) -> bool:
            return False

        def select_next_waypoint(self, include_failed: bool = False) -> Waypoint | None:
            include_failed_calls.append(include_failed)
            self._select_calls += 1
            if self._select_calls == 1:
                return self.flight_plan.waypoints[0]
            return None

        async def execute_waypoint(self, *args, **kwargs) -> ExecutionResult:  # type: ignore[no-untyped-def]
            return ExecutionResult.FAILED

        def handle_execution_result(self, waypoint, result) -> NextAction:  # type: ignore[no-untyped-def]
            return NextAction(
                action="intervention", waypoint=waypoint, message="failed"
            )

        def mark_waypoint_status(self, waypoint, status) -> None:  # type: ignore[no-untyped-def]
            waypoint.status = status

    monkeypatch.setattr(
        "waypoints.orchestration.coordinator.JourneyCoordinator", FakeCoordinator
    )

    exit_code = cmd_run(_build_args(project.slug, "abort"))
    out = capsys.readouterr()

    assert exit_code == 1
    assert include_failed_calls == [False]
    assert "Aborting due to failure (--on-error=abort)" in out.out


def test_cmd_run_skip_continues_after_failed_waypoint(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project, _ = _create_project_with_plan(tmp_path, count=2)
    include_failed_calls: list[bool] = []

    class FakeCoordinator:
        def __init__(self, project, flight_plan) -> None:  # type: ignore[no-untyped-def]
            self.flight_plan = flight_plan
            self._select_calls = 0
            self._exec_calls = 0

        def reset_stale_in_progress(self) -> bool:
            return False

        def select_next_waypoint(self, include_failed: bool = False) -> Waypoint | None:
            include_failed_calls.append(include_failed)
            self._select_calls += 1
            if self._select_calls <= 2:
                return self.flight_plan.waypoints[self._select_calls - 1]
            return None

        async def execute_waypoint(self, *args, **kwargs) -> ExecutionResult:  # type: ignore[no-untyped-def]
            self._exec_calls += 1
            return (
                ExecutionResult.FAILED
                if self._exec_calls == 1
                else ExecutionResult.SUCCESS
            )

        def handle_execution_result(self, waypoint, result) -> NextAction:  # type: ignore[no-untyped-def]
            if result == ExecutionResult.SUCCESS:
                return NextAction(action="complete", message="done")
            return NextAction(
                action="intervention", waypoint=waypoint, message="failed"
            )

        def mark_waypoint_status(self, waypoint, status) -> None:  # type: ignore[no-untyped-def]
            waypoint.status = status

    monkeypatch.setattr(
        "waypoints.orchestration.coordinator.JourneyCoordinator", FakeCoordinator
    )

    exit_code = cmd_run(_build_args(project.slug, "skip"))
    out = capsys.readouterr()

    assert exit_code == 1
    assert include_failed_calls == [False, False]
    assert "Skipping to next waypoint" in out.out
    assert "Summary: 1 completed, 1 failed, 0 skipped" in out.out


def test_cmd_run_retry_retries_failed_waypoint_with_include_failed(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project, _ = _create_project_with_plan(tmp_path, count=1)
    include_failed_calls: list[bool] = []

    class FakeCoordinator:
        def __init__(self, project, flight_plan) -> None:  # type: ignore[no-untyped-def]
            self.flight_plan = flight_plan
            self._select_calls = 0
            self._exec_calls = 0

        def reset_stale_in_progress(self) -> bool:
            return False

        def select_next_waypoint(self, include_failed: bool = False) -> Waypoint | None:
            include_failed_calls.append(include_failed)
            self._select_calls += 1
            if self._select_calls <= 2:
                return self.flight_plan.waypoints[0]
            return None

        async def execute_waypoint(self, *args, **kwargs) -> ExecutionResult:  # type: ignore[no-untyped-def]
            self._exec_calls += 1
            return (
                ExecutionResult.FAILED
                if self._exec_calls == 1
                else ExecutionResult.SUCCESS
            )

        def handle_execution_result(self, waypoint, result) -> NextAction:  # type: ignore[no-untyped-def]
            if result == ExecutionResult.SUCCESS:
                return NextAction(action="complete", message="done")
            return NextAction(
                action="intervention", waypoint=waypoint, message="failed"
            )

        def mark_waypoint_status(self, waypoint, status) -> None:  # type: ignore[no-untyped-def]
            waypoint.status = status

    monkeypatch.setattr(
        "waypoints.orchestration.coordinator.JourneyCoordinator", FakeCoordinator
    )

    exit_code = cmd_run(_build_args(project.slug, "retry"))
    out = capsys.readouterr()

    assert exit_code == 1
    assert include_failed_calls == [True, True]
    assert out.out.count("Executing: WP-001 - Waypoint 1") == 2


def test_cmd_run_abort_returns_two_on_intervention(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project, _ = _create_project_with_plan(tmp_path, count=1)
    marked: list[WaypointStatus] = []

    class FakeCoordinator:
        def __init__(self, project, flight_plan) -> None:  # type: ignore[no-untyped-def]
            self.flight_plan = flight_plan
            self._select_calls = 0

        def reset_stale_in_progress(self) -> bool:
            return False

        def select_next_waypoint(self, include_failed: bool = False) -> Waypoint | None:
            self._select_calls += 1
            if self._select_calls == 1:
                return self.flight_plan.waypoints[0]
            return None

        async def execute_waypoint(self, waypoint, *args, **kwargs) -> ExecutionResult:  # type: ignore[no-untyped-def]
            intervention = Intervention(
                type=InterventionType.EXECUTION_ERROR,
                waypoint=waypoint,
                iteration=1,
                max_iterations=3,
                error_summary="Needs intervention",
            )
            raise InterventionNeededError(intervention)

        def handle_execution_result(self, waypoint, result) -> NextAction:  # type: ignore[no-untyped-def]
            return NextAction(action="pause")

        def mark_waypoint_status(self, waypoint, status) -> None:  # type: ignore[no-untyped-def]
            waypoint.status = status
            marked.append(status)

    monkeypatch.setattr(
        "waypoints.orchestration.coordinator.JourneyCoordinator", FakeCoordinator
    )

    exit_code = cmd_run(_build_args(project.slug, "abort"))
    out = capsys.readouterr()

    assert exit_code == 2
    assert marked == [WaypointStatus.FAILED]
    assert "Aborting due to intervention (--on-error=abort)" in out.out


def test_cmd_run_skip_marks_intervention_as_skipped(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project, _ = _create_project_with_plan(tmp_path, count=1)
    marked: list[WaypointStatus] = []

    class FakeCoordinator:
        def __init__(self, project, flight_plan) -> None:  # type: ignore[no-untyped-def]
            self.flight_plan = flight_plan
            self._select_calls = 0

        def reset_stale_in_progress(self) -> bool:
            return False

        def select_next_waypoint(self, include_failed: bool = False) -> Waypoint | None:
            self._select_calls += 1
            if self._select_calls == 1:
                return self.flight_plan.waypoints[0]
            return None

        async def execute_waypoint(self, waypoint, *args, **kwargs) -> ExecutionResult:  # type: ignore[no-untyped-def]
            intervention = Intervention(
                type=InterventionType.BUDGET_EXCEEDED,
                waypoint=waypoint,
                iteration=1,
                max_iterations=3,
                error_summary="Budget reached",
            )
            raise InterventionNeededError(intervention)

        def handle_execution_result(self, waypoint, result) -> NextAction:  # type: ignore[no-untyped-def]
            return NextAction(action="pause")

        def mark_waypoint_status(self, waypoint, status) -> None:  # type: ignore[no-untyped-def]
            waypoint.status = status
            marked.append(status)

    monkeypatch.setattr(
        "waypoints.orchestration.coordinator.JourneyCoordinator", FakeCoordinator
    )

    exit_code = cmd_run(_build_args(project.slug, "skip"))
    out = capsys.readouterr()

    assert exit_code == 0
    assert marked == [WaypointStatus.FAILED, WaypointStatus.SKIPPED]
    assert "Skipping to next waypoint" in out.out
    assert "Summary: 0 completed, 0 failed, 1 skipped" in out.out
