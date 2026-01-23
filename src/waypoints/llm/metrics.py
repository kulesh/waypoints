"""Metrics and cost tracking for LLM calls.

This module provides comprehensive metrics tracking for all LLM interactions,
including cost tracking, latency measurement, and optional budget enforcement.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import TYPE_CHECKING, Any

from waypoints.models.schema import migrate_if_needed, write_schema_fields

if TYPE_CHECKING:
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)


@dataclass
class LLMCall:
    """Record of a single LLM API call.

    Token counts and cost are recorded when available from the provider.
    """

    call_id: str
    phase: str  # spark, shape, chart, fly
    waypoint_id: str | None  # If during FLY phase
    cost_usd: float | None
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int
    model: str
    timestamp: datetime
    success: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data: dict[str, Any] = {
            "call_id": self.call_id,
            "phase": self.phase,
            "waypoint_id": self.waypoint_id,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "model": self.model,
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "error": self.error,
        }
        if self.tokens_in is not None:
            data["tokens_in"] = self.tokens_in
        if self.tokens_out is not None:
            data["tokens_out"] = self.tokens_out
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMCall":
        """Create from dictionary."""
        return cls(
            call_id=data["call_id"],
            phase=data["phase"],
            waypoint_id=data.get("waypoint_id"),
            cost_usd=data.get("cost_usd"),
            tokens_in=data.get("tokens_in"),
            tokens_out=data.get("tokens_out"),
            latency_ms=data["latency_ms"],
            model=data["model"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            success=data["success"],
            error=data.get("error"),
        )

    @classmethod
    def create(
        cls,
        phase: str,
        cost_usd: float | None,
        latency_ms: int,
        model: str = "claude-sonnet-4",
        waypoint_id: str | None = None,
        success: bool = True,
        error: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> "LLMCall":
        """Create a new LLMCall with auto-generated ID and timestamp."""
        return cls(
            call_id=str(uuid.uuid4())[:8],
            phase=phase,
            waypoint_id=waypoint_id,
            cost_usd=cost_usd,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            model=model,
            timestamp=datetime.now(UTC),
            success=success,
            error=error,
        )


class MetricsCollector:
    """Collects and aggregates LLM call metrics.

    Metrics are persisted to a JSONL file and can be queried for
    aggregations like total cost, cost by phase, and cost by waypoint.
    """

    def __init__(self, project: "Project") -> None:
        """Initialize the collector.

        Args:
            project: The project to collect metrics for.
        """
        self.path = project.get_path() / "metrics.jsonl"
        self._calls: list[LLMCall] = []
        self._load()

    def _load(self) -> None:
        """Load existing metrics from file.

        Automatically migrates legacy files to current schema version.
        """
        if not self.path.exists():
            return

        # Migrate legacy files if needed
        migrate_if_needed(self.path, "metrics")

        try:
            with open(self.path) as f:
                for line_num, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    # Skip header line (has _schema field)
                    if line_num == 0 and "_schema" in data:
                        continue

                    self._calls.append(LLMCall.from_dict(data))
            logger.debug("Loaded %d metrics from %s", len(self._calls), self.path)
        except Exception as e:
            logger.warning("Failed to load metrics from %s: %s", self.path, e)

    def record(self, call: LLMCall) -> None:
        """Record a new LLM call.

        Args:
            call: The LLMCall to record.
        """
        self._calls.append(call)
        self._append(call)
        logger.debug(
            "Recorded call %s: phase=%s, cost=$%.4f",
            call.call_id,
            call.phase,
            call.cost_usd or 0.0,
        )

    def _append(self, call: LLMCall) -> None:
        """Append a call to the metrics file."""
        # Ensure directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Write header if file doesn't exist
        if not self.path.exists():
            header = {
                **write_schema_fields("metrics"),
                "created_at": datetime.now(UTC).isoformat(),
            }
            with open(self.path, "w") as f:
                f.write(json.dumps(header) + "\n")

        with open(self.path, "a") as f:
            f.write(json.dumps(call.to_dict()) + "\n")

    @property
    def total_cost(self) -> float:
        """Get total cost across all calls."""
        return sum(c.cost_usd or 0.0 for c in self._calls)

    @property
    def total_tokens_in(self) -> int:
        """Get total input tokens across all calls."""
        return sum(c.tokens_in or 0 for c in self._calls)

    @property
    def total_tokens_out(self) -> int:
        """Get total output tokens across all calls."""
        return sum(c.tokens_out or 0 for c in self._calls)

    @property
    def total_calls(self) -> int:
        """Get total number of calls."""
        return len(self._calls)

    def cost_by_phase(self) -> dict[str, float]:
        """Get cost breakdown by phase.

        Returns:
            Dictionary mapping phase name to total cost.
        """
        result: dict[str, float] = {}
        for call in self._calls:
            result[call.phase] = result.get(call.phase, 0) + (call.cost_usd or 0.0)
        return result

    def cost_by_waypoint(self) -> dict[str, float]:
        """Get cost breakdown by waypoint.

        Returns:
            Dictionary mapping waypoint ID to total cost.
        """
        result: dict[str, float] = {}
        for call in self._calls:
            if call.waypoint_id and call.cost_usd is not None:
                result[call.waypoint_id] = (
                    result.get(call.waypoint_id, 0) + call.cost_usd
                )
        return result

    def tokens_by_phase(self) -> dict[str, tuple[int, int]]:
        """Get token breakdown by phase.

        Returns:
            Dictionary mapping phase name to (tokens_in, tokens_out).
        """
        result: dict[str, list[int]] = {}
        for call in self._calls:
            tokens_in = call.tokens_in or 0
            tokens_out = call.tokens_out or 0
            if tokens_in == 0 and tokens_out == 0:
                continue
            current = result.setdefault(call.phase, [0, 0])
            current[0] += tokens_in
            current[1] += tokens_out
        return {phase: (vals[0], vals[1]) for phase, vals in result.items()}

    def tokens_by_waypoint(self) -> dict[str, tuple[int, int]]:
        """Get token breakdown by waypoint.

        Returns:
            Dictionary mapping waypoint ID to (tokens_in, tokens_out).
        """
        result: dict[str, list[int]] = {}
        for call in self._calls:
            if not call.waypoint_id:
                continue
            tokens_in = call.tokens_in or 0
            tokens_out = call.tokens_out or 0
            if tokens_in == 0 and tokens_out == 0:
                continue
            current = result.setdefault(call.waypoint_id, [0, 0])
            current[0] += tokens_in
            current[1] += tokens_out
        return {wp_id: (vals[0], vals[1]) for wp_id, vals in result.items()}

    def summary(self) -> dict[str, Any]:
        """Get aggregated metrics summary.

        Returns:
            Dictionary with total_calls, total_cost_usd, cost_by_phase,
            avg_latency_ms, and success_rate.
        """
        return {
            "total_calls": len(self._calls),
            "total_cost_usd": self.total_cost,
            "cost_by_phase": self.cost_by_phase(),
            "cost_by_waypoint": self.cost_by_waypoint(),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "tokens_by_phase": self.tokens_by_phase(),
            "tokens_by_waypoint": self.tokens_by_waypoint(),
            "avg_latency_ms": (
                mean(c.latency_ms for c in self._calls) if self._calls else 0
            ),
            "success_rate": (
                sum(1 for c in self._calls if c.success) / len(self._calls)
                if self._calls
                else 1.0
            ),
        }


class BudgetExceededError(Exception):
    """Raised when a budget limit is exceeded."""

    def __init__(
        self, limit_type: str, current_value: float, limit_value: float
    ) -> None:
        self.limit_type = limit_type
        self.current_value = current_value
        self.limit_value = limit_value
        super().__init__(
            f"Budget exceeded: {limit_type} is {current_value:.2f}, "
            f"limit is {limit_value:.2f}"
        )


@dataclass
class Budget:
    """Optional budget constraints for LLM usage.

    Set limits on cost to prevent runaway spending.
    """

    max_usd: float | None = None

    def check(self, collector: MetricsCollector) -> None:
        """Check if the budget has been exceeded.

        Args:
            collector: The MetricsCollector to check against.

        Raises:
            BudgetExceededError: If any limit is exceeded.
        """
        if self.max_usd is not None and collector.total_cost > self.max_usd:
            raise BudgetExceededError("cost", collector.total_cost, self.max_usd)

    def remaining(self, collector: MetricsCollector) -> float | None:
        """Get remaining budget.

        Returns:
            Remaining USD or None if no budget set.
        """
        if self.max_usd is None:
            return None
        return max(0, self.max_usd - collector.total_cost)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {"max_usd": self.max_usd}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Budget":
        """Create from dictionary."""
        return cls(max_usd=data.get("max_usd"))
