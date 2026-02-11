"""Tests for FlyPhase internals that should stay UI-agnostic."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionType,
)
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.fly_phase import FlyPhase
from waypoints.orchestration.types import RollbackResult


class _ExecutorLogStub:
    def __init__(self) -> None:
        self.pause_calls = 0
        self.git_calls: list[tuple[bool, str, str]] = []
        self.intervention_calls: list[tuple[str, dict[str, Any]]] = []

    def log_pause_event(self) -> None:
        self.pause_calls += 1

    def log_git_commit_event(
        self, success: bool, commit_hash: str, message: str
    ) -> None:
        self.git_calls.append((success, commit_hash, message))

    def log_intervention_resolved_event(self, action: str, **params: Any) -> None:
        self.intervention_calls.append((action, params))


def _phase() -> FlyPhase:
    return FlyPhase(SimpleNamespace())  # type: ignore[arg-type]


@dataclass
class _GitContextStub:
    is_repo: bool = True
    head_commit: str | None = "deadbeef"

    def is_git_repo(self) -> bool:
        return self.is_repo

    def get_head_commit(self) -> str | None:
        return self.head_commit


class _CoordinatorStub:
    def __init__(
        self,
        *,
        flight_plan: FlightPlan | None = None,
        git: _GitContextStub | None = None,
    ) -> None:
        self.flight_plan = flight_plan
        self.git = git or _GitContextStub()
        self.current_waypoint: Waypoint | None = None
        self.save_calls = 0
        self.project = SimpleNamespace()

    def save_flight_plan(self) -> None:
        self.save_calls += 1


def test_log_pause_uses_executor_public_api() -> None:
    phase = _phase()
    stub = _ExecutorLogStub()
    phase._active_executor = stub  # type: ignore[assignment]

    phase.log_pause()

    assert stub.pause_calls == 1


def test_log_git_commit_uses_executor_public_api() -> None:
    phase = _phase()
    stub = _ExecutorLogStub()
    phase._active_executor = stub  # type: ignore[assignment]

    phase.log_git_commit(success=True, commit_hash="abc123", message="done")

    assert stub.git_calls == [(True, "abc123", "done")]


def test_log_intervention_resolved_uses_executor_public_api() -> None:
    phase = _phase()
    stub = _ExecutorLogStub()
    phase._active_executor = stub  # type: ignore[assignment]

    phase.log_intervention_resolved("retry", additional_iterations=3)

    assert stub.intervention_calls == [("retry", {"additional_iterations": 3})]


def test_classify_intervention_seeds_last_safe_ref_from_head() -> None:
    coordinator = _CoordinatorStub(flight_plan=FlightPlan(), git=_GitContextStub())
    phase = FlyPhase(coordinator)  # type: ignore[arg-type]
    waypoint = Waypoint(id="WP-001", title="Test", objective="Test objective")
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
    )

    presentation = phase.classify_intervention(intervention)

    assert presentation.show_modal is True
    assert intervention.context["last_safe_ref"] == "HEAD"
    assert intervention.context["last_safe_commit"] == "deadbeef"


def test_handle_intervention_rollback_success_resets_waypoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waypoint = Waypoint(
        id="WP-001",
        title="Rollback target",
        objective="Rollback",
        status=WaypointStatus.FAILED,
    )
    initial_plan = FlightPlan(waypoints=[waypoint])
    coordinator = _CoordinatorStub(flight_plan=initial_plan, git=_GitContextStub())
    phase = FlyPhase(coordinator)  # type: ignore[arg-type]
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
        context={"last_safe_ref": "HEAD"},
    )
    calls: list[str | None] = []

    rolled_back_waypoint = Waypoint(
        id="WP-001",
        title="Rollback target",
        objective="Rollback",
        status=WaypointStatus.FAILED,
    )
    rolled_back_plan = FlightPlan(waypoints=[rolled_back_waypoint])

    def _fake_rollback(tag: str | None) -> RollbackResult:
        calls.append(tag)
        coordinator.flight_plan = rolled_back_plan
        return RollbackResult(
            success=True,
            message="Rolled back to HEAD (deadbeef)",
            resolved_ref="HEAD",
            flight_plan=rolled_back_plan,
        )

    monkeypatch.setattr(phase, "rollback_to_tag", _fake_rollback)

    action = phase.handle_intervention(
        intervention=intervention,
        action=InterventionAction.ROLLBACK,
    )

    assert calls == ["HEAD"]
    assert action.action == "pause"
    assert "Waypoint reset to PENDING" in (action.message or "")
    assert rolled_back_waypoint.status == WaypointStatus.PENDING
    assert coordinator.save_calls == 1
    assert coordinator.current_waypoint is None


def test_handle_intervention_rollback_failure_pauses_with_fix_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waypoint = Waypoint(
        id="WP-001",
        title="Rollback target",
        objective="Rollback",
        status=WaypointStatus.FAILED,
    )
    initial_plan = FlightPlan(waypoints=[waypoint])
    coordinator = _CoordinatorStub(flight_plan=initial_plan, git=_GitContextStub())
    phase = FlyPhase(coordinator)  # type: ignore[arg-type]
    intervention = Intervention(
        type=InterventionType.EXECUTION_ERROR,
        waypoint=waypoint,
        iteration=2,
        max_iterations=5,
        error_summary="Failed",
    )

    monkeypatch.setattr(
        phase,
        "rollback_to_tag",
        lambda tag: RollbackResult(
            success=False,
            message="No rollback reference available.",
            resolved_ref=tag,
            flight_plan=None,
        ),
    )

    action = phase.handle_intervention(
        intervention=intervention,
        action=InterventionAction.ROLLBACK,
    )

    assert action.action == "pause"
    assert "Rollback failed:" in (action.message or "")
    assert "git add -A && git commit" in (action.message or "")
    assert waypoint.status == WaypointStatus.FAILED
    assert coordinator.save_calls == 0
