"""Tests for the flight test runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from scripts.run_flight_test import run


def _setup_flight_test(root: Path, *, min_files: list[str]) -> Path:
    flight_test = root / "flight-test"
    (flight_test / "input").mkdir(parents=True)
    (flight_test / "expected").mkdir(parents=True)

    (flight_test / "input" / "idea.txt").write_text(
        "A test idea", encoding="utf-8"
    )
    (flight_test / "expected" / "min_files.txt").write_text(
        "\n".join(min_files) + "\n", encoding="utf-8"
    )
    return flight_test


def _latest_results(results_dir: Path) -> Path:
    results = sorted(results_dir.iterdir())
    assert results
    return results[-1]


def test_runner_records_success(tmp_path: Path) -> None:
    flight_test = _setup_flight_test(tmp_path, min_files=["README.md"])
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "README.md").write_text("ok", encoding="utf-8")

    exit_code = run(flight_test, project_path, skip_smoke=True)

    assert exit_code == 0
    results_dir = _latest_results(flight_test / "results")
    meta = json.loads((results_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["success"] is True
    assert (results_dir / "idea.txt").exists()


def test_runner_detects_missing_files(tmp_path: Path) -> None:
    flight_test = _setup_flight_test(tmp_path, min_files=["README.md", "missing.txt"])
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "README.md").write_text("ok", encoding="utf-8")

    exit_code = run(flight_test, project_path, skip_smoke=True)

    assert exit_code == 1
    results_dir = _latest_results(flight_test / "results")
    meta = json.loads((results_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["success"] is False
    assert "missing.txt" in meta["missing_files"]


def test_runner_writes_smoke_log(tmp_path: Path) -> None:
    flight_test = _setup_flight_test(tmp_path, min_files=["README.md"])
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "README.md").write_text("ok", encoding="utf-8")

    smoke_script = flight_test / "expected" / "smoke_test.sh"
    smoke_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    smoke_script.chmod(0o755)

    exit_code = run(flight_test, project_path, skip_smoke=False)

    assert exit_code == 0
    results_dir = _latest_results(flight_test / "results")
    meta = json.loads((results_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["smoke_test"]["ran"] is True
    log_path = Path(meta["smoke_test"]["log_path"])
    assert log_path.exists()
