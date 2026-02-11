"""Tests for FlyPhase internals that should stay UI-agnostic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from waypoints.orchestration.fly_phase import FlyPhase


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
