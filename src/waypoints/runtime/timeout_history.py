"""In-process adaptive timeout history for command runs."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from waypoints.runtime.timeout_policy import TimeoutDomain


@dataclass(frozen=True, slots=True)
class TimeoutStatsSnapshot:
    """Snapshot of timeout behavior for a command key."""

    key: str
    runs: int
    timeout_count: int
    success_count: int
    max_duration_seconds: float
    p90_duration_seconds: float | None


class TimeoutHistory:
    """Tracks command durations and timeouts to tune future budgets."""

    def __init__(self, max_samples_per_key: int = 20) -> None:
        self._max_samples_per_key = max_samples_per_key
        self._durations: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=max_samples_per_key)
        )
        self._runs: dict[str, int] = defaultdict(int)
        self._timeouts: dict[str, int] = defaultdict(int)

    def record(self, key: str, duration_seconds: float, timed_out: bool) -> None:
        """Record one command attempt result."""
        self._runs[key] += 1
        self._durations[key].append(max(0.0, duration_seconds))
        if timed_out:
            self._timeouts[key] += 1

    def recommended_timeout_seconds(
        self,
        key: str,
        fallback_seconds: float,
        *,
        ceiling_seconds: float,
    ) -> float:
        """Recommend a future timeout based on observed command durations."""
        durations = self._durations.get(key)
        if not durations:
            return fallback_seconds

        observed_max = max(durations)
        timeout_rate = 0.0
        runs = self._runs.get(key, 0)
        if runs > 0:
            timeout_rate = self._timeouts.get(key, 0) / runs

        # Keep headroom above observed max; increase headroom when timeouts occur.
        headroom = 1.5 + min(timeout_rate, 0.5)
        recommended = observed_max * headroom
        return max(fallback_seconds, min(recommended, ceiling_seconds))

    def snapshot(self, key: str) -> TimeoutStatsSnapshot:
        """Get current stats snapshot for a key."""
        durations = self._durations.get(key, deque())
        runs = self._runs.get(key, 0)
        timeout_count = self._timeouts.get(key, 0)
        max_duration = max(durations) if durations else 0.0
        p90 = _percentile(durations, 0.9)

        return TimeoutStatsSnapshot(
            key=key,
            runs=runs,
            timeout_count=timeout_count,
            success_count=max(0, runs - timeout_count),
            max_duration_seconds=max_duration,
            p90_duration_seconds=p90,
        )


def build_command_key(
    domain: TimeoutDomain,
    command: str,
    *,
    category: str | None = None,
    cwd: Path | None = None,
) -> str:
    """Build normalized key for timeout history lookup."""
    normalized = " ".join(command.split())
    category_key = category or "-"
    cwd_key = str(cwd) if cwd is not None else "-"
    return f"{domain.value}|{category_key}|{cwd_key}|{normalized}"


def _percentile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = int((len(ordered) - 1) * q)
    return ordered[index]


_DEFAULT_TIMEOUT_HISTORY = TimeoutHistory()


def get_timeout_history() -> TimeoutHistory:
    """Return shared in-process timeout history."""
    return _DEFAULT_TIMEOUT_HISTORY
