"""Tests for flight test discovery and execution helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from waypoints.flight_tests.runner import (
    FlightTestCase,
    FlightTestStatus,
    discover_flight_tests,
    execute_flight_tests,
    parse_level_selector,
    validate_flight_test_case,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_parse_level_selector_handles_single_and_range() -> None:
    levels = parse_level_selector("L0,L2-L4")
    assert levels == {0, 2, 3, 4}


def test_parse_level_selector_rejects_invalid_tokens() -> None:
    with pytest.raises(ValueError, match="Invalid level token"):
        _ = parse_level_selector("L0-L2-L3")


def test_discover_flight_tests_finds_level_directories(tmp_path: Path) -> None:
    (tmp_path / "L0-hello-world").mkdir()
    (tmp_path / "L1-todo-cli").mkdir()
    (tmp_path / "self-host").mkdir()
    (tmp_path / "notes").mkdir()

    cases = discover_flight_tests(tmp_path)

    assert [case.case_id for case in cases] == ["L0-hello-world", "L1-todo-cli"]
    assert [case.level for case in cases] == [0, 1]


def test_validate_flight_test_case_detects_missing_inputs(tmp_path: Path) -> None:
    case_path = tmp_path / "L0-hello-world"
    case_path.mkdir()
    case = FlightTestCase(
        case_id="L0-hello-world",
        level=0,
        slug="hello-world",
        path=case_path,
        idea_file=case_path / "input" / "idea.txt",
        smoke_test_script=case_path / "expected" / "smoke_test.sh",
    )

    issues = validate_flight_test_case(case)
    assert len(issues) == 2
    assert "missing idea file" in issues[0]
    assert "missing smoke test script" in issues[1]


def test_execute_flight_tests_plans_without_running(tmp_path: Path) -> None:
    case_path = tmp_path / "L0-hello-world"
    _write(case_path / "input" / "idea.txt", "hello")
    _write(case_path / "expected" / "smoke_test.sh", "#!/usr/bin/env bash\nexit 0\n")
    case = discover_flight_tests(tmp_path)[0]

    results = execute_flight_tests(
        [case],
        generated_projects_root=tmp_path / "generated",
        execute=False,
        timeout_seconds=1,
    )

    assert len(results) == 1
    assert results[0].status == FlightTestStatus.PLANNED


def test_execute_flight_tests_runs_smoke_test(tmp_path: Path) -> None:
    case_path = tmp_path / "L0-hello-world"
    _write(case_path / "input" / "idea.txt", "hello")
    _write(case_path / "expected" / "smoke_test.sh", "#!/usr/bin/env bash\nexit 0\n")
    case = discover_flight_tests(tmp_path)[0]

    generated_root = tmp_path / "generated"
    (generated_root / "L0-hello-world").mkdir(parents=True)

    results = execute_flight_tests(
        [case],
        generated_projects_root=generated_root,
        execute=True,
        timeout_seconds=1,
    )

    assert len(results) == 1
    assert results[0].status == FlightTestStatus.PASSED
    assert results[0].return_code == 0
