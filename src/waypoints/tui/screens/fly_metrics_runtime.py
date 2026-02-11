"""Live metrics overlay runtime for the Fly screen."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from waypoints.fly.types import ExecutionContext
from waypoints.models.project import Project
from waypoints.tui.screens.fly_projections import (
    MetricsCollectorLike,
    build_project_metrics_projection,
    lookup_waypoint_cached_tokens_in,
    lookup_waypoint_cost,
    lookup_waypoint_tokens,
)


class WaypointDetailMetricsLike(Protocol):
    """Detail panel contract needed for live metrics rendering."""

    def update_metrics(
        self,
        cost: float | None,
        tokens: tuple[int, int] | None,
        cached_tokens_in: int | None,
    ) -> None: ...


class WaypointListMetricsLike(Protocol):
    """Waypoint list panel contract needed for project metrics rendering."""

    def update_project_metrics(
        self,
        cost: float,
        time_seconds: int,
        tokens_in: int | None,
        tokens_out: int | None,
        tokens_known: bool,
        cached_tokens_in: int | None,
        cached_tokens_known: bool,
    ) -> None: ...


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass
class LiveMetricsOverlay:
    """Tracks in-flight metrics and overlays them on persisted collector values."""

    waypoint_id: str | None = None
    waypoint_cost: float | None = None
    waypoint_tokens_in: int = 0
    waypoint_tokens_out: int = 0
    waypoint_tokens_known: bool = False
    waypoint_cached_tokens_in: int = 0
    waypoint_cached_tokens_known: bool = False
    project_cost: float = 0.0
    project_tokens_in: int = 0
    project_tokens_out: int = 0
    project_tokens_known: bool = False
    project_cached_tokens_in: int = 0
    project_cached_tokens_known: bool = False
    project_time_seconds: int = 0

    def reset(self) -> None:
        """Clear live overlay state."""
        self.waypoint_id = None
        self.waypoint_cost = None
        self.waypoint_tokens_in = 0
        self.waypoint_tokens_out = 0
        self.waypoint_tokens_known = False
        self.waypoint_cached_tokens_in = 0
        self.waypoint_cached_tokens_known = False
        self.project_cost = 0.0
        self.project_tokens_in = 0
        self.project_tokens_out = 0
        self.project_tokens_known = False
        self.project_cached_tokens_in = 0
        self.project_cached_tokens_known = False
        self.project_time_seconds = 0

    def seed(
        self,
        *,
        waypoint_id: str,
        project: Project,
        metrics_collector: MetricsCollectorLike | None,
    ) -> None:
        """Initialize live state from current persisted metrics baselines."""
        self.waypoint_id = waypoint_id
        projection = build_project_metrics_projection(
            project=project,
            metrics_collector=metrics_collector,
        )
        self.project_cost = projection.cost
        self.project_tokens_in = projection.tokens_in or 0
        self.project_tokens_out = projection.tokens_out or 0
        self.project_tokens_known = projection.tokens_known
        self.project_cached_tokens_in = projection.cached_tokens_in or 0
        self.project_cached_tokens_known = projection.cached_tokens_known
        self.project_time_seconds = projection.time_seconds

        self.waypoint_cost = lookup_waypoint_cost(metrics_collector, waypoint_id)
        waypoint_tokens = lookup_waypoint_tokens(metrics_collector, waypoint_id)
        if waypoint_tokens is not None:
            self.waypoint_tokens_in = waypoint_tokens[0]
            self.waypoint_tokens_out = waypoint_tokens[1]
            self.waypoint_tokens_known = True
        else:
            self.waypoint_tokens_in = 0
            self.waypoint_tokens_out = 0
            self.waypoint_tokens_known = False
        waypoint_cached = lookup_waypoint_cached_tokens_in(
            metrics_collector, waypoint_id
        )
        if waypoint_cached is not None:
            self.waypoint_cached_tokens_in = waypoint_cached
            self.waypoint_cached_tokens_known = True
        else:
            self.waypoint_cached_tokens_in = 0
            self.waypoint_cached_tokens_known = False

    def apply_update(self, ctx: ExecutionContext) -> bool:
        """Apply one `metrics_updated` event. Returns True when overlay was updated."""
        if ctx.step != "metrics_updated":
            return False
        raw_metrics = ctx.metadata.get("metrics")
        if not isinstance(raw_metrics, dict):
            return False
        waypoint_id = raw_metrics.get("waypoint_id")
        if not isinstance(waypoint_id, str) or waypoint_id != self.waypoint_id:
            return False

        if (
            waypoint_cost := _as_float(raw_metrics.get("waypoint_cost_usd"))
        ) is not None:
            self.waypoint_cost = waypoint_cost
        elif (delta_cost := _as_float(raw_metrics.get("delta_cost_usd"))) is not None:
            self.waypoint_cost = (self.waypoint_cost or 0.0) + delta_cost

        if (project_cost := _as_float(raw_metrics.get("project_cost_usd"))) is not None:
            self.project_cost = project_cost
        elif (delta_cost := _as_float(raw_metrics.get("delta_cost_usd"))) is not None:
            self.project_cost += delta_cost

        if raw_metrics.get("tokens_known") is True:
            self.waypoint_tokens_known = True
            self.project_tokens_known = True
        if raw_metrics.get("cached_tokens_known") is True:
            self.waypoint_cached_tokens_known = True
            self.project_cached_tokens_known = True

        if (
            waypoint_tokens_in := _as_int(raw_metrics.get("waypoint_tokens_in"))
        ) is not None:
            self.waypoint_tokens_in = waypoint_tokens_in
            self.waypoint_tokens_known = True
        elif (
            delta_tokens_in := _as_int(raw_metrics.get("delta_tokens_in"))
        ) is not None:
            self.waypoint_tokens_in += delta_tokens_in
            self.waypoint_tokens_known = True

        if (
            waypoint_tokens_out := _as_int(raw_metrics.get("waypoint_tokens_out"))
        ) is not None:
            self.waypoint_tokens_out = waypoint_tokens_out
            self.waypoint_tokens_known = True
        elif (
            delta_tokens_out := _as_int(raw_metrics.get("delta_tokens_out"))
        ) is not None:
            self.waypoint_tokens_out += delta_tokens_out
            self.waypoint_tokens_known = True

        if (
            waypoint_cached := _as_int(raw_metrics.get("waypoint_cached_tokens_in"))
        ) is not None:
            self.waypoint_cached_tokens_in = waypoint_cached
            self.waypoint_cached_tokens_known = True
        elif (
            delta_cached := _as_int(raw_metrics.get("delta_cached_tokens_in"))
        ) is not None:
            self.waypoint_cached_tokens_in += delta_cached
            self.waypoint_cached_tokens_known = True

        if (
            project_tokens_in := _as_int(raw_metrics.get("project_tokens_in"))
        ) is not None:
            self.project_tokens_in = project_tokens_in
            self.project_tokens_known = True
        elif (
            delta_tokens_in := _as_int(raw_metrics.get("delta_tokens_in"))
        ) is not None:
            self.project_tokens_in += delta_tokens_in
            self.project_tokens_known = True

        if (
            project_tokens_out := _as_int(raw_metrics.get("project_tokens_out"))
        ) is not None:
            self.project_tokens_out = project_tokens_out
            self.project_tokens_known = True
        elif (
            delta_tokens_out := _as_int(raw_metrics.get("delta_tokens_out"))
        ) is not None:
            self.project_tokens_out += delta_tokens_out
            self.project_tokens_known = True

        if (
            project_cached := _as_int(raw_metrics.get("project_cached_tokens_in"))
        ) is not None:
            self.project_cached_tokens_in = project_cached
            self.project_cached_tokens_known = True
        elif (
            delta_cached := _as_int(raw_metrics.get("delta_cached_tokens_in"))
        ) is not None:
            self.project_cached_tokens_in += delta_cached
            self.project_cached_tokens_known = True
        return True

    def get_waypoint_cost(
        self,
        *,
        waypoint_id: str,
        metrics_collector: MetricsCollectorLike | None,
    ) -> float | None:
        """Resolve waypoint cost from overlay first, then collector."""
        if waypoint_id == self.waypoint_id and self.waypoint_cost is not None:
            return self.waypoint_cost
        return lookup_waypoint_cost(metrics_collector, waypoint_id)

    def get_waypoint_tokens(
        self,
        *,
        waypoint_id: str,
        metrics_collector: MetricsCollectorLike | None,
    ) -> tuple[int, int] | None:
        """Resolve waypoint tokens from overlay first, then collector."""
        if waypoint_id == self.waypoint_id:
            if self.waypoint_tokens_known:
                return (self.waypoint_tokens_in, self.waypoint_tokens_out)
            return None
        return lookup_waypoint_tokens(metrics_collector, waypoint_id)

    def get_waypoint_cached_tokens_in(
        self,
        *,
        waypoint_id: str,
        metrics_collector: MetricsCollectorLike | None,
    ) -> int | None:
        """Resolve waypoint cached input tokens from overlay first, then collector."""
        if waypoint_id == self.waypoint_id:
            if self.waypoint_cached_tokens_known:
                return self.waypoint_cached_tokens_in
            return None
        return lookup_waypoint_cached_tokens_in(metrics_collector, waypoint_id)

    def update_widgets(
        self,
        *,
        detail_panel: WaypointDetailMetricsLike,
        list_panel: WaypointListMetricsLike,
    ) -> None:
        """Render live overlay metrics into detail and list panels."""
        detail_panel.update_metrics(
            self.waypoint_cost,
            (
                (self.waypoint_tokens_in, self.waypoint_tokens_out)
                if self.waypoint_tokens_known
                else None
            ),
            (
                self.waypoint_cached_tokens_in
                if self.waypoint_cached_tokens_known
                else None
            ),
        )
        list_panel.update_project_metrics(
            self.project_cost,
            self.project_time_seconds,
            (self.project_tokens_in if self.project_tokens_known else None),
            (self.project_tokens_out if self.project_tokens_known else None),
            self.project_tokens_known,
            (
                self.project_cached_tokens_in
                if self.project_cached_tokens_known
                else None
            ),
            self.project_cached_tokens_known,
        )
