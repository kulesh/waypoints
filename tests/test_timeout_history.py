"""Tests for timeout history persistence and autosave behavior."""

from __future__ import annotations

import json
from pathlib import Path

from waypoints.runtime.timeout_history import PersistentTimeoutHistory, TimeoutHistory


def test_timeout_history_save_load_round_trip(tmp_path: Path) -> None:
    history = TimeoutHistory(max_samples_per_key=3)
    key = "host_validation|lint|-|cargo clippy"

    history.record(key, 10.0, timed_out=False)
    history.record(key, 20.0, timed_out=False)
    history.record(key, 30.0, timed_out=True)
    history.record(key, 40.0, timed_out=False)

    out_path = tmp_path / "timeout-history.json"
    history.save(out_path)

    loaded = TimeoutHistory.load(out_path)
    snapshot = loaded.snapshot(key)

    # max_samples_per_key=3, so oldest sample is evicted
    assert snapshot.runs == 4
    assert snapshot.timeout_count == 1
    assert snapshot.max_duration_seconds == 40.0
    assert snapshot.p90_duration_seconds == 30.0


def test_timeout_history_load_invalid_json_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "timeout-history.json"
    path.write_text("{not-json", encoding="utf-8")

    history = TimeoutHistory.load(path)

    snapshot = history.snapshot("missing")
    assert snapshot.runs == 0
    assert snapshot.timeout_count == 0


def test_persistent_timeout_history_autosaves(tmp_path: Path) -> None:
    path = tmp_path / "timeout-history.json"
    history = PersistentTimeoutHistory.open(path, autosave_every=2)
    key = "llm_tool_bash|-|-|echo hi"

    history.record(key, 0.2, timed_out=False)
    assert not path.exists()

    history.record(key, 0.4, timed_out=True)
    assert path.exists()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "keys" in payload
    keys = payload["keys"]
    assert isinstance(keys, dict)
    assert key in keys
