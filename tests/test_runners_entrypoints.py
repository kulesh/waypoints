"""Smoke tests for runner entrypoint error handling."""

from __future__ import annotations

import argparse
from io import StringIO
from typing import Any

import pytest

from waypoints.runners import (
    run_chart,
    run_fly,
    run_shape_brief,
    run_shape_spec,
    run_spark,
)


class _TTYStringIO(StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


def test_run_chart_requires_input_or_stdin(monkeypatch: Any) -> None:
    monkeypatch.setattr(run_chart.sys, "argv", ["run_chart", "--project", "demo"])
    monkeypatch.setattr(run_chart.sys, "stdin", _TTYStringIO(""))

    code = run_chart.main()

    assert code == 1


def test_run_shape_brief_requires_input_or_stdin(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        run_shape_brief.sys,
        "argv",
        ["run_shape_brief", "--project", "demo"],
    )
    monkeypatch.setattr(run_shape_brief.sys, "stdin", _TTYStringIO(""))

    code = run_shape_brief.main()

    assert code == 1


def test_run_shape_spec_requires_input_or_stdin(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        run_shape_spec.sys,
        "argv",
        ["run_shape_spec", "--project", "demo"],
    )
    monkeypatch.setattr(run_shape_spec.sys, "stdin", _TTYStringIO(""))

    code = run_shape_spec.main()

    assert code == 1


def test_run_spark_requires_idea_or_stdin(monkeypatch: Any) -> None:
    monkeypatch.setattr(run_spark.sys, "argv", ["run_spark", "--project", "demo"])
    monkeypatch.setattr(run_spark.sys, "stdin", _TTYStringIO(""))

    code = run_spark.main()

    assert code == 1


@pytest.mark.anyio
async def test_run_fly_returns_error_when_project_missing(monkeypatch: Any) -> None:
    def missing_project(_: str) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(run_fly.Project, "load", missing_project)

    args = argparse.Namespace(
        project="missing",
        waypoint=None,
        max_iterations=3,
        host_validations_enabled=True,
        skip_intervention=False,
        continue_on_error=False,
        verbose=False,
    )
    code = await run_fly.run_execution(args)

    assert code == 1


@pytest.mark.anyio
async def test_run_fly_returns_error_when_flight_plan_missing(monkeypatch: Any) -> None:
    class _Project:
        name = "Demo"

    monkeypatch.setattr(run_fly.Project, "load", lambda _: _Project())
    monkeypatch.setattr(run_fly.FlightPlanReader, "load", lambda _: None)

    args = argparse.Namespace(
        project="demo",
        waypoint=None,
        max_iterations=3,
        host_validations_enabled=True,
        skip_intervention=False,
        continue_on_error=False,
        verbose=False,
    )
    code = await run_fly.run_execution(args)

    assert code == 1
