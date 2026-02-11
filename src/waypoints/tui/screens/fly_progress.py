"""Progress rendering helpers for Fly screen."""

from __future__ import annotations

from typing import Protocol

from rich.text import Text

from waypoints.fly.evidence import FileOperation
from waypoints.fly.executor import ExecutionContext


class ExecutionLogLike(Protocol):
    """Minimal execution log contract used for progress rendering."""

    def write(self, renderable: object) -> object: ...

    def write_log(self, message: str) -> None: ...

    def log_heading(self, message: str) -> None: ...

    def log_success(self, message: str) -> None: ...

    def log_error(self, message: str) -> None: ...


class DetailPanelLike(Protocol):
    """Minimal detail panel contract used for progress rendering."""

    @property
    def execution_log(self) -> ExecutionLogLike: ...

    def is_showing_output_for(self, waypoint_id: str) -> bool: ...

    def update_iteration(self, iteration: int, total: int) -> None: ...

    def update_criteria(self, completed: set[int]) -> None: ...

    def apply_agent_progress(self, ctx: ExecutionContext) -> None: ...


def apply_progress_update(
    *,
    ctx: ExecutionContext,
    detail_panel: DetailPanelLike,
    live_criteria_completed: set[int],
) -> set[int]:
    """Apply one execution progress update to the detail panel."""
    if not detail_panel.is_showing_output_for(ctx.waypoint.id):
        return live_criteria_completed

    log = detail_panel.execution_log
    if ctx.step != "protocol_artifact":
        detail_panel.update_iteration(ctx.iteration, ctx.total_iterations)
    detail_panel.apply_agent_progress(ctx)

    next_criteria_completed = live_criteria_completed
    if ctx.criteria_completed:
        next_criteria_completed = ctx.criteria_completed
        detail_panel.update_criteria(ctx.criteria_completed)

    if ctx.step == "executing":
        log.log_heading(f"Iteration {ctx.iteration}/{ctx.total_iterations}")
    elif ctx.step == "tool_use":
        _render_tool_use(log=log, ctx=ctx)
    elif ctx.step == "streaming":
        output = ctx.output.strip()
        if output:
            log.write_log(output)
    elif ctx.step == "complete":
        log.log_success(ctx.output)
    elif ctx.step == "error":
        log.log_error(ctx.output)
    elif ctx.step == "stage":
        log.log_heading(f"Stage: {ctx.output}")
    elif ctx.step == "finalizing":
        output = ctx.output.strip()
        if output:
            log.log_heading(f"Verifier: {output}")
    elif ctx.step == "protocol_artifact":
        output = ctx.output.strip()
        if output:
            log.write_log(f"[dim]↳ {output}[/]")
    elif ctx.step == "validation_failed":
        log.log_error(ctx.output)
    elif ctx.step == "clarification_pending":
        log.write_log(f"[yellow]⚠ {ctx.output}[/]")
    elif ctx.step == "warning":
        log.write_log(f"[yellow]⚠ {ctx.output}[/]")

    return next_criteria_completed


def _render_tool_use(*, log: ExecutionLogLike, ctx: ExecutionContext) -> None:
    if ctx.file_operations:
        _write_file_operation(log=log, op=ctx.file_operations[-1])
        return
    output = ctx.output.strip()
    if output:
        log.write_log(f"[dim]→ {output}[/]")


def _write_file_operation(*, log: ExecutionLogLike, op: FileOperation) -> None:
    if not op.file_path:
        return

    icon = {
        "Edit": "✎",
        "Write": "✚",
        "Read": "📖",
        "Bash": "$",
        "Glob": "🔍",
        "Grep": "🔍",
    }.get(op.tool_name, "•")
    style = "dim" if op.tool_name == "Read" else "cyan"

    if op.tool_name in ("Edit", "Write", "Read"):
        escaped_path = op.file_path.replace("'", "\\'")
        markup = (
            f"  [{style}]{icon}[/] "
            f"[@click=screen.preview_file('{escaped_path}')]"
            f"[{style} underline]{op.file_path}[/][/]"
        )
        log.write(markup)
        return

    text = f"  [{style}]{icon}[/] {op.file_path}"
    log.write(Text.from_markup(text))
