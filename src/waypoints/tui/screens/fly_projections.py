"""Projection and metrics helpers for Fly screen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from waypoints.fly.execution_log import ExecutionLogReader
from waypoints.models.project import Project


class MetricsCollectorLike(Protocol):
    """Minimal metrics collector contract used by Fly projections."""

    @property
    def total_cost(self) -> float: ...

    @property
    def total_tokens_in(self) -> int: ...

    @property
    def total_tokens_out(self) -> int: ...

    @property
    def total_cached_tokens_in(self) -> int: ...

    def has_token_usage_data(self) -> bool: ...

    def has_cached_token_usage_data(self) -> bool: ...

    def cost_by_waypoint(self) -> dict[str, float]: ...

    def tokens_by_waypoint(self) -> dict[str, tuple[int, int]]: ...

    def cached_tokens_by_waypoint(self) -> dict[str, int]: ...


class CompletionStatusLike(Protocol):
    """Completion status contract returned by coordinator."""

    @property
    def pending(self) -> int: ...

    @property
    def in_progress(self) -> int: ...

    @property
    def failed(self) -> int: ...

    @property
    def blocked(self) -> int: ...

    @property
    def all_complete(self) -> bool: ...


@dataclass(frozen=True)
class ProjectMetricsProjection:
    """Aggregated metrics values displayed in the waypoint list panel."""

    cost: float
    time_seconds: int
    tokens_in: int | None
    tokens_out: int | None
    tokens_known: bool
    cached_tokens_in: int | None
    cached_tokens_known: bool


def calculate_total_execution_time(project: Project) -> int:
    """Calculate total execution time across all waypoints in seconds."""
    total_seconds = 0
    log_files = ExecutionLogReader.list_logs(project)
    for log_path in log_files:
        try:
            log = ExecutionLogReader.load(log_path)
            if log.completed_at and log.started_at:
                elapsed = (log.completed_at - log.started_at).total_seconds()
                total_seconds += int(elapsed)
        except Exception:
            continue
    return total_seconds


def build_project_metrics_projection(
    *,
    project: Project,
    metrics_collector: MetricsCollectorLike | None,
) -> ProjectMetricsProjection:
    """Build project-wide cost/time/token metrics projection."""
    cost = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_known = False
    cached_tokens_in: int | None = None
    cached_tokens_known = False

    if metrics_collector is not None:
        cost = metrics_collector.total_cost
        tokens_in = metrics_collector.total_tokens_in
        tokens_out = metrics_collector.total_tokens_out
        cached_tokens_in = metrics_collector.total_cached_tokens_in
        tokens_known = metrics_collector.has_token_usage_data()
        cached_tokens_known = metrics_collector.has_cached_token_usage_data()

    return ProjectMetricsProjection(
        cost=cost,
        time_seconds=calculate_total_execution_time(project),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_known=tokens_known,
        cached_tokens_in=cached_tokens_in,
        cached_tokens_known=cached_tokens_known,
    )


def lookup_waypoint_cost(
    metrics_collector: MetricsCollectorLike | None, waypoint_id: str
) -> float | None:
    """Get cost for a waypoint from metrics."""
    if metrics_collector is None:
        return None
    return metrics_collector.cost_by_waypoint().get(waypoint_id)


def lookup_waypoint_tokens(
    metrics_collector: MetricsCollectorLike | None, waypoint_id: str
) -> tuple[int, int] | None:
    """Get token totals for a waypoint from metrics."""
    if metrics_collector is None:
        return None
    return metrics_collector.tokens_by_waypoint().get(waypoint_id)


def lookup_waypoint_cached_tokens_in(
    metrics_collector: MetricsCollectorLike | None, waypoint_id: str
) -> int | None:
    """Get cached input tokens for a waypoint from metrics."""
    if metrics_collector is None:
        return None
    return metrics_collector.cached_tokens_by_waypoint().get(waypoint_id)


def build_completion_status_projection(
    status: CompletionStatusLike,
) -> tuple[bool, int, int, int]:
    """Build legacy completion tuple used by Fly screen state messaging."""
    pending = status.pending + status.in_progress
    return (status.all_complete, pending, status.failed, status.blocked)
