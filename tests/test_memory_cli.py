"""Tests for memory CLI commands."""

import argparse
from pathlib import Path

from waypoints.config.settings import settings
from waypoints.main import cmd_memory
from waypoints.models.project import Project


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
