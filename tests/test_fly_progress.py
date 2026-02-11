"""Tests for Fly screen progress rendering helpers."""

from __future__ import annotations

from waypoints.fly.evidence import FileOperation
from waypoints.fly.executor import ExecutionContext
from waypoints.models.waypoint import Waypoint
from waypoints.tui.screens.fly_progress import apply_progress_update


class _Log:
    def __init__(self) -> None:
        self.entries: list[tuple[str, object]] = []

    def write(self, renderable: object) -> None:
        self.entries.append(("write", renderable))

    def write_log(self, message: str) -> None:
        self.entries.append(("write_log", message))

    def log_heading(self, message: str) -> None:
        self.entries.append(("heading", message))

    def log_success(self, message: str) -> None:
        self.entries.append(("success", message))

    def log_error(self, message: str) -> None:
        self.entries.append(("error", message))


class _DetailPanel:
    def __init__(self, *, showing_waypoint_id: str) -> None:
        self._showing_waypoint_id = showing_waypoint_id
        self.log = _Log()
        self.iteration_updates: list[tuple[int, int]] = []
        self.criteria_updates: list[set[int]] = []

    @property
    def execution_log(self) -> _Log:
        return self.log

    def is_showing_output_for(self, waypoint_id: str) -> bool:
        return waypoint_id == self._showing_waypoint_id

    def update_iteration(self, iteration: int, total: int) -> None:
        self.iteration_updates.append((iteration, total))

    def update_criteria(self, completed: set[int]) -> None:
        self.criteria_updates.append(completed)


def _context(
    *,
    step: str,
    output: str = "",
    criteria_completed: set[int] | None = None,
    file_operations: list[FileOperation] | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        waypoint=Waypoint(id="WP-001", title="Title", objective="Objective"),
        iteration=2,
        total_iterations=8,
        step=step,
        output=output,
        criteria_completed=criteria_completed or set(),
        file_operations=file_operations or [],
    )


def test_apply_progress_update_ignores_non_visible_waypoint() -> None:
    detail_panel = _DetailPanel(showing_waypoint_id="WP-999")
    original = {9}

    returned = apply_progress_update(
        ctx=_context(step="streaming", output="hello"),
        detail_panel=detail_panel,
        live_criteria_completed=original,
    )

    assert returned == {9}
    assert detail_panel.iteration_updates == []
    assert detail_panel.criteria_updates == []
    assert detail_panel.execution_log.entries == []


def test_apply_progress_update_renders_clickable_edit_operation() -> None:
    detail_panel = _DetailPanel(showing_waypoint_id="WP-001")

    returned = apply_progress_update(
        ctx=_context(
            step="tool_use",
            criteria_completed={1, 2},
            file_operations=[FileOperation(tool_name="Edit", file_path="src/app.py")],
        ),
        detail_panel=detail_panel,
        live_criteria_completed=set(),
    )

    assert returned == {1, 2}
    assert detail_panel.iteration_updates == [(2, 8)]
    assert detail_panel.criteria_updates == [{1, 2}]
    assert len(detail_panel.execution_log.entries) == 1
    entry_type, content = detail_panel.execution_log.entries[0]
    assert entry_type == "write"
    assert "screen.preview_file('src/app.py')" in str(content)


def test_apply_progress_update_renders_output_for_tool_use_without_file_ops() -> None:
    detail_panel = _DetailPanel(showing_waypoint_id="WP-001")

    apply_progress_update(
        ctx=_context(step="tool_use", output="  command output  "),
        detail_panel=detail_panel,
        live_criteria_completed=set(),
    )

    assert detail_panel.execution_log.entries == [
        ("write_log", "[dim]→ command output[/]")
    ]


def test_apply_progress_update_handles_complete_error_and_stage_steps() -> None:
    detail_panel = _DetailPanel(showing_waypoint_id="WP-001")

    apply_progress_update(
        ctx=_context(step="complete", output="ok"),
        detail_panel=detail_panel,
        live_criteria_completed=set(),
    )
    apply_progress_update(
        ctx=_context(step="error", output="boom"),
        detail_panel=detail_panel,
        live_criteria_completed=set(),
    )
    apply_progress_update(
        ctx=_context(step="stage", output="verifying"),
        detail_panel=detail_panel,
        live_criteria_completed=set(),
    )

    assert detail_panel.execution_log.entries == [
        ("success", "ok"),
        ("error", "boom"),
        ("heading", "Stage: verifying"),
    ]
