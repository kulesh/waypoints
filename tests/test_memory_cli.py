"""Tests for memory CLI commands."""

import argparse
from pathlib import Path

from waypoints.config.settings import settings
from waypoints.main import cmd_memory
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader, FlightPlanWriter
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint
from waypoints.spec import compute_spec_hash


def test_cmd_memory_refresh_single_project(tmp_path: Path) -> None:
    """`waypoints memory refresh <slug>` should refresh memory for one project."""
    settings.project_directory = tmp_path
    project = Project.create("Memory Single")

    args = argparse.Namespace(
        memory_action="refresh",
        all=False,
        project=project.slug,
        init_overrides=True,
    )
    exit_code = cmd_memory(args)

    assert exit_code == 0
    memory_root = project.get_path() / ".waypoints" / "memory"
    assert (memory_root / "project-index.v1.json").exists()
    assert (memory_root / "policy-overrides.v1.json").exists()


def test_cmd_memory_refresh_defaults_to_all_projects(tmp_path: Path) -> None:
    """`waypoints memory refresh` without slug should refresh all projects."""
    settings.project_directory = tmp_path
    project_a = Project.create("Memory A")
    project_b = Project.create("Memory B")

    args = argparse.Namespace(
        memory_action="refresh",
        all=False,
        project=None,
        init_overrides=False,
    )
    exit_code = cmd_memory(args)

    assert exit_code == 0
    for project in (project_a, project_b):
        memory_root = project.get_path() / ".waypoints" / "memory"
        assert (memory_root / "project-index.v1.json").exists()


def test_cmd_memory_refresh_spec_context_single_project(tmp_path: Path) -> None:
    """`waypoints memory refresh-spec-context` should update waypoint fields."""
    settings.project_directory = tmp_path
    project = Project.create("Spec Context")
    project.get_docs_path().mkdir(parents=True, exist_ok=True)
    (project.get_docs_path() / "product-spec.md").write_text(
        "# Product Spec\n\n## Scope\nImplement query parser and runner.\n",
        encoding="utf-8",
    )
    plan = FlightPlan(
        waypoints=[
            Waypoint(
                id="WP-001",
                title="Build parser",
                objective="Implement parser module",
                acceptance_criteria=["Parses user queries"],
            )
        ]
    )
    FlightPlanWriter(project).save(plan)

    args = argparse.Namespace(
        memory_action="refresh-spec-context",
        all=False,
        project=project.slug,
        only_stale=False,
    )
    exit_code = cmd_memory(args)

    assert exit_code == 0
    refreshed = FlightPlanReader.load(project)
    assert refreshed is not None
    waypoint = refreshed.waypoints[0]
    assert waypoint.spec_context_summary
    assert waypoint.spec_section_refs
    assert waypoint.spec_context_hash == compute_spec_hash(
        "# Product Spec\n\n## Scope\nImplement query parser and runner.\n"
    )


def test_cmd_memory_refresh_spec_context_only_stale_preserves_current(
    tmp_path: Path,
) -> None:
    """Only-stale mode should not rewrite current waypoint context."""
    settings.project_directory = tmp_path
    project = Project.create("Spec Context Stable")
    spec = "# Product Spec\n\n## Scope\nImplement query parser and runner.\n"
    spec_hash = compute_spec_hash(spec)
    project.get_docs_path().mkdir(parents=True, exist_ok=True)
    (project.get_docs_path() / "product-spec.md").write_text(spec, encoding="utf-8")
    plan = FlightPlan(
        waypoints=[
            Waypoint(
                id="WP-001",
                title="Build parser",
                objective="Implement parser module",
                acceptance_criteria=["Parses user queries"],
                spec_context_summary="Current summary should remain unchanged.",
                spec_section_refs=["Scope"],
                spec_context_hash=spec_hash,
            )
        ]
    )
    FlightPlanWriter(project).save(plan)

    args = argparse.Namespace(
        memory_action="refresh-spec-context",
        all=False,
        project=project.slug,
        only_stale=True,
    )
    exit_code = cmd_memory(args)

    assert exit_code == 0
    refreshed = FlightPlanReader.load(project)
    assert refreshed is not None
    waypoint = refreshed.waypoints[0]
    assert waypoint.spec_context_summary == "Current summary should remain unchanged."
    assert waypoint.spec_section_refs == ["Scope"]
    assert waypoint.spec_context_hash == spec_hash
