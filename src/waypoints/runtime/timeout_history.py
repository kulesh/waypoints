"""In-process adaptive timeout history for command runs."""

from __future__ import annotations

import atexit
import json
import logging
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from waypoints.config.paths import get_paths
from waypoints.runtime.timeout_policy import TimeoutDomain

logger = logging.getLogger(__name__)
HISTORY_SCHEMA_VERSION = 1


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

    @property
    def max_samples_per_key(self) -> int:
        """Maximum retained duration samples per command key."""
        return self._max_samples_per_key

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

    def to_dict(self) -> dict[str, object]:
        """Serialize timeout history to a JSON-compatible dictionary."""
        keys: dict[str, dict[str, object]] = {}
        all_keys = set(self._runs) | set(self._timeouts) | set(self._durations)
        for key in all_keys:
            keys[key] = {
                "runs": self._runs.get(key, 0),
                "timeouts": self._timeouts.get(key, 0),
                "durations": list(self._durations.get(key, deque())),
            }

        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "max_samples_per_key": self._max_samples_per_key,
            "keys": keys,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, object],
        *,
        max_samples_per_key: int = 20,
    ) -> "TimeoutHistory":
        """Deserialize timeout history from a JSON-compatible dictionary."""
        raw_max = data.get("max_samples_per_key")
        sample_limit = (
            int(raw_max)
            if isinstance(raw_max, int) and raw_max > 0
            else max_samples_per_key
        )
        history = cls(max_samples_per_key=sample_limit)
        raw_keys = data.get("keys")
        if not isinstance(raw_keys, dict):
            return history

        for key, payload in raw_keys.items():
            if not isinstance(key, str):
                continue
            if not isinstance(payload, dict):
                continue

            runs = payload.get("runs", 0)
            timeouts = payload.get("timeouts", 0)
            durations = payload.get("durations", [])

            if isinstance(runs, int) and runs >= 0:
                history._runs[key] = runs
            if isinstance(timeouts, int) and timeouts >= 0:
                history._timeouts[key] = timeouts
            if isinstance(durations, list):
                filtered: list[float] = []
                for value in durations:
                    if isinstance(value, (int, float)):
                        filtered.append(max(0.0, float(value)))
                history._durations[key] = deque(
                    filtered[-history._max_samples_per_key :],
                    maxlen=history._max_samples_per_key,
                )

        return history

    def save(self, path: Path) -> None:
        """Persist timeout history to disk."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        max_samples_per_key: int = 20,
    ) -> "TimeoutHistory":
        """Load timeout history from disk, falling back to empty state."""
        if not path.exists():
            return cls(max_samples_per_key=max_samples_per_key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load timeout history %s: %s", path, exc)
            return cls(max_samples_per_key=max_samples_per_key)
        if not isinstance(raw, dict):
            return cls(max_samples_per_key=max_samples_per_key)
        return cls.from_dict(raw, max_samples_per_key=max_samples_per_key)


class PersistentTimeoutHistory(TimeoutHistory):
    """Timeout history that periodically persists updates to disk."""

    def __init__(
        self,
        *,
        storage_path: Path,
        max_samples_per_key: int = 20,
        autosave_every: int = 5,
    ) -> None:
        super().__init__(max_samples_per_key=max_samples_per_key)
        self._storage_path = storage_path
        self._autosave_every = max(1, autosave_every)
        self._pending_writes = 0

    @classmethod
    def open(
        cls,
        storage_path: Path,
        *,
        max_samples_per_key: int = 20,
        autosave_every: int = 5,
    ) -> "PersistentTimeoutHistory":
        """Create persistent timeout history by loading existing state."""
        loaded = TimeoutHistory.load(
            storage_path,
            max_samples_per_key=max_samples_per_key,
        )
        history = cls(
            storage_path=storage_path,
            max_samples_per_key=loaded.max_samples_per_key,
            autosave_every=autosave_every,
        )
        history._durations.update(loaded._durations)
        history._runs.update(loaded._runs)
        history._timeouts.update(loaded._timeouts)
        return history

    def record(self, key: str, duration_seconds: float, timed_out: bool) -> None:
        """Record one command attempt and trigger periodic persistence."""
        super().record(key, duration_seconds, timed_out)
        self._pending_writes += 1
        if self._pending_writes >= self._autosave_every:
            self.flush()

    def flush(self) -> None:
        """Persist pending updates to disk."""
        if self._pending_writes == 0 and self._storage_path.exists():
            return
        try:
            self.save(self._storage_path)
            self._pending_writes = 0
        except OSError as exc:
            logger.warning(
                "Failed to persist timeout history %s: %s",
                self._storage_path,
                exc,
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
_DEFAULT_PERSISTENT_TIMEOUT_HISTORY: PersistentTimeoutHistory | None = None


def get_timeout_history() -> TimeoutHistory:
    """Return shared timeout history with persistent backing."""
    global _DEFAULT_PERSISTENT_TIMEOUT_HISTORY
    if _DEFAULT_PERSISTENT_TIMEOUT_HISTORY is None:
        paths = get_paths()
        paths.ensure_global_dirs()
        storage_path = paths.global_state_dir / "timeout-history.json"
        _DEFAULT_PERSISTENT_TIMEOUT_HISTORY = PersistentTimeoutHistory.open(
            storage_path=storage_path,
            max_samples_per_key=_DEFAULT_TIMEOUT_HISTORY.max_samples_per_key,
        )
        atexit.register(_DEFAULT_PERSISTENT_TIMEOUT_HISTORY.flush)
    return _DEFAULT_PERSISTENT_TIMEOUT_HISTORY
