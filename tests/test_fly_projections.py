"""Tests for Fly screen projections helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from waypoints.tui.screens import fly_projections


class _Project:
    def get_sessions_path(self) -> Path:
        return Path("/tmp")


class _Metrics:
    total_cost = 1.25
    total_tokens_in = 111
    total_tokens_out = 222
    total_cached_tokens_in = 333

    def has_token_usage_data(self) -> bool:
        return True

    def has_cached_token_usage_data(self) -> bool:
        return True

    def cost_by_waypoint(self) -> dict[str, float]:
        return {"WP-001": 0.42}

    def tokens_by_waypoint(self) -> dict[str, tuple[int, int]]:
        return {"WP-001": (10, 20)}

    def cached_tokens_by_waypoint(self) -> dict[str, int]:
        return {"WP-001": 5}


def test_calculate_total_execution_time_ignores_invalid_logs(monkeypatch) -> None:
    logs = [Path("log-a.jsonl"), Path("log-b.jsonl"), Path("log-c.jsonl")]
    start = datetime(2026, 2, 10, 10, 0, 0, tzinfo=UTC)

    monkeypatch.setattr(
        fly_projections.ExecutionLogReader,
        "list_logs",
        staticmethod(lambda _project: logs),
    )

    def _load(log_path: Path) -> SimpleNamespace:
        if log_path == logs[0]:
            return SimpleNamespace(
                started_at=start,
                completed_at=datetime(2026, 2, 10, 10, 0, 30, tzinfo=UTC),
            )
        if log_path == logs[1]:
            raise RuntimeError("bad log")
        return SimpleNamespace(started_at=start, completed_at=None)

    monkeypatch.setattr(fly_projections.ExecutionLogReader, "load", staticmethod(_load))

    assert fly_projections.calculate_total_execution_time(_Project()) == 30


def test_build_project_metrics_projection_with_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        fly_projections,
        "calculate_total_execution_time",
        lambda _project: 12,
    )

    projection = fly_projections.build_project_metrics_projection(
        project=_Project(),
        metrics_collector=_Metrics(),
    )

    assert projection.cost == 1.25
    assert projection.time_seconds == 12
    assert projection.tokens_in == 111
    assert projection.tokens_out == 222
    assert projection.tokens_known is True
    assert projection.cached_tokens_in == 333
    assert projection.cached_tokens_known is True


def test_build_project_metrics_projection_without_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        fly_projections,
        "calculate_total_execution_time",
        lambda _project: 7,
    )

    projection = fly_projections.build_project_metrics_projection(
        project=_Project(),
        metrics_collector=None,
    )

    assert projection.cost == 0.0
    assert projection.time_seconds == 7
    assert projection.tokens_in is None
    assert projection.tokens_out is None
    assert projection.tokens_known is False
    assert projection.cached_tokens_in is None
    assert projection.cached_tokens_known is False


def test_waypoint_metric_lookups() -> None:
    metrics = _Metrics()

    assert fly_projections.lookup_waypoint_cost(metrics, "WP-001") == 0.42
    assert fly_projections.lookup_waypoint_tokens(metrics, "WP-001") == (10, 20)
    assert fly_projections.lookup_waypoint_cached_tokens_in(metrics, "WP-001") == 5

    assert fly_projections.lookup_waypoint_cost(None, "WP-001") is None
    assert fly_projections.lookup_waypoint_tokens(None, "WP-001") is None
    assert fly_projections.lookup_waypoint_cached_tokens_in(None, "WP-001") is None


def test_build_completion_status_projection() -> None:
    status = SimpleNamespace(
        all_complete=False,
        pending=2,
        in_progress=1,
        failed=3,
        blocked=4,
    )

    assert fly_projections.build_completion_status_projection(status) == (
        False,
        3,
        3,
        4,
    )
