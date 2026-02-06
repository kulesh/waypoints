"""Tests for waypoint-scoped durable memory."""

from pathlib import Path

from waypoints.memory.waypoint_memory import (
    WaypointMemoryRecord,
    build_waypoint_memory_context,
    save_waypoint_memory,
)
from waypoints.models.waypoint import Waypoint


def test_save_waypoint_memory_round_trip(tmp_path: Path) -> None:
    """Saved waypoint memory should round-trip with stable fields."""
    record = WaypointMemoryRecord(
        schema_version="v1",
        saved_at_utc="2026-02-07T01:00:00+00:00",
        waypoint_id="WP-010",
        title="Implement search",
        objective="Add fast fuzzy search",
        dependencies=("WP-002",),
        result="success",
        iterations_used=2,
        max_iterations=10,
        protocol_derailments=("claimed completion without exact completion marker",),
        error_summary=None,
        changed_files=("src/search.py", "tests/test_search.py"),
        approx_tokens_changed=420,
        validation_commands=("pytest -v", "ruff check ."),
        useful_commands=("pytest -v", "ruff check ."),
        verified_criteria=(0, 1),
    )
    save_waypoint_memory(tmp_path, record)

    context = build_waypoint_memory_context(
        project_root=tmp_path,
        waypoint=Waypoint(
            id="WP-011",
            title="Search UX",
            objective="Integrate search navigation",
            dependencies=["WP-010"],
        ),
    )

    assert "WP-010 (dependency" in context
    assert "src/search.py" in context
    assert "pytest -v" in context


def test_memory_context_is_char_bounded(tmp_path: Path) -> None:
    """Context builder should honor max char cap for prompt budgets."""
    record = WaypointMemoryRecord(
        schema_version="v1",
        saved_at_utc="2026-02-07T01:00:00+00:00",
        waypoint_id="WP-100",
        title="x" * 300,
        objective="y" * 300,
        dependencies=(),
        result="intervention_needed",
        iterations_used=10,
        max_iterations=10,
        protocol_derailments=("attempted tool access to blocked project areas",),
        error_summary="z" * 400,
        changed_files=("src/large_file.py",),
        approx_tokens_changed=2000,
        validation_commands=("pytest -v",),
        useful_commands=("pytest -v",),
        verified_criteria=(),
    )
    save_waypoint_memory(tmp_path, record)

    context = build_waypoint_memory_context(
        project_root=tmp_path,
        waypoint=Waypoint(
            id="WP-101",
            title="new waypoint",
            objective="consume memory context",
        ),
        max_chars=120,
    )

    assert len(context) <= 120
