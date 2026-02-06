"""Tests for persistent project memory index."""

from pathlib import Path

from waypoints.memory import (
    format_directory_policy_for_prompt,
    load_or_build_project_memory,
    memory_dir,
)


def test_load_or_build_project_memory_persists_index_files(tmp_path: Path) -> None:
    """Memory build should persist stack, directory map, and index payloads."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n", encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / ".venv").mkdir()
    (tmp_path / "sessions").mkdir()

    memory = load_or_build_project_memory(tmp_path)

    memory_root = memory_dir(tmp_path)
    assert (memory_root / "stack-profile.v1.json").exists()
    assert (memory_root / "directory-map.v1.json").exists()
    assert (memory_root / "project-index.v1.json").exists()

    blocked = set(memory.index.blocked_top_level_dirs)
    assert ".git" in blocked
    assert ".waypoints" in blocked
    assert "sessions" in blocked
    assert ".venv" in blocked

    focus = set(memory.index.focus_top_level_dirs)
    assert "src" in focus
    assert "tests" in focus

    summary = format_directory_policy_for_prompt(memory.index)
    assert "Focus your search in" in summary
    assert "Tool access is blocked for" in summary


def test_project_memory_rebuilds_when_top_level_layout_changes(tmp_path: Path) -> None:
    """Adding top-level stack markers should trigger refreshed policy."""
    (tmp_path / "src").mkdir()
    first = load_or_build_project_memory(tmp_path)
    assert "node_modules" not in set(first.index.blocked_top_level_dirs)

    (tmp_path / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    (tmp_path / "node_modules").mkdir()

    refreshed = load_or_build_project_memory(tmp_path)
    assert "node_modules" in set(refreshed.index.blocked_top_level_dirs)
