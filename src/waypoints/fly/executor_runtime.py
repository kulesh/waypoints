"""Non-orchestrator runtime helpers for waypoint execution."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Protocol, Sequence

from waypoints.fly.evidence import FileOperation
from waypoints.fly.provenance import WorkspaceDiffSummary
from waypoints.fly.types import ExecutionMetricsUpdate, _LoopState
from waypoints.git.receipt import CapturedEvidence, CriterionVerification
from waypoints.llm.providers.base import StreamComplete
from waypoints.memory import WaypointMemoryRecord, save_waypoint_memory
from waypoints.models.waypoint import Waypoint

if TYPE_CHECKING:
    from waypoints.fly.execution_log import ExecutionLogWriter
    from waypoints.llm.metrics import MetricsCollector


class MetricsCollectorLike(Protocol):
    """Minimal metrics collector shape used for progress snapshots."""

    @property
    def total_cost(self) -> float: ...

    @property
    def total_tokens_in(self) -> int: ...

    @property
    def total_tokens_out(self) -> int: ...

    @property
    def total_cached_tokens_in(self) -> int: ...


def dedupe_non_blank(values: Iterable[str]) -> tuple[str, ...]:
    """Return values in stable order with blank entries removed."""
    return tuple(dict.fromkeys(value for value in values if value.strip()))


def build_protocol_progress_payload(
    artifact: object,
) -> tuple[str, dict[str, object]] | None:
    """Build live-progress payload from a protocol artifact-like object."""
    to_dict = getattr(artifact, "to_dict", None)
    if not callable(to_dict):
        return None
    payload = to_dict()
    if not isinstance(payload, dict):
        return None
    artifact_payload: dict[str, object] = {
        str(key): value for key, value in payload.items()
    }
    artifact_type = str(payload.get("artifact_type", "protocol_artifact"))
    role = str(payload.get("produced_by_role", "orchestrator"))
    return (
        f"{role}:{artifact_type}",
        {
            "role": role,
            "artifact": artifact_payload,
        },
    )


def truncate_output_tail(output: str | None, *, max_chars: int = 800) -> str | None:
    """Trim output to an intervention-friendly tail snippet."""
    if not output:
        return None
    cleaned = output.strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    return f"...{cleaned[-max_chars:]}"


def summarize_failed_bash_command(
    *,
    command: object,
    tool_output: str | None,
) -> str | None:
    """Build a concise failed-command summary for intervention surfacing."""
    if not isinstance(command, str) or not command.strip():
        return None
    command_block = command.strip()
    if len(command_block) > 500:
        command_block = command_block[:500] + "..."
    details = [f"Failed command:\n{command_block}"]
    output_tail = truncate_output_tail(tool_output)
    if output_tail:
        details.append(f"Last command output (tail):\n{output_tail}")
    return "\n\n".join(details)


def build_metrics_progress_payload(
    *,
    role: str,
    waypoint_id: str,
    delta_cost_usd: float | None,
    delta_tokens_in: int | None,
    delta_tokens_out: int | None,
    delta_cached_tokens_in: int | None,
    waypoint_cost_usd: float,
    waypoint_tokens_in: int,
    waypoint_tokens_out: int,
    waypoint_cached_tokens_in: int,
    waypoint_tokens_known: bool,
    waypoint_cached_tokens_known: bool,
    metrics_collector: "MetricsCollector | MetricsCollectorLike | None",
) -> tuple[str, dict[str, object]]:
    """Build a normalized metrics update payload for live progress callbacks."""
    project_cost_usd: float | None = None
    project_tokens_in: int | None = None
    project_tokens_out: int | None = None
    project_cached_tokens_in: int | None = None
    if metrics_collector is not None:
        project_cost_usd = metrics_collector.total_cost + (delta_cost_usd or 0.0)
        project_tokens_in = metrics_collector.total_tokens_in + (delta_tokens_in or 0)
        project_tokens_out = metrics_collector.total_tokens_out + (
            delta_tokens_out or 0
        )
        project_cached_tokens_in = metrics_collector.total_cached_tokens_in + (
            delta_cached_tokens_in or 0
        )
    metrics = ExecutionMetricsUpdate(
        role=role,
        waypoint_id=waypoint_id,
        delta_cost_usd=delta_cost_usd,
        delta_tokens_in=delta_tokens_in,
        delta_tokens_out=delta_tokens_out,
        delta_cached_tokens_in=delta_cached_tokens_in,
        waypoint_cost_usd=waypoint_cost_usd,
        waypoint_tokens_in=(waypoint_tokens_in if waypoint_tokens_known else None),
        waypoint_tokens_out=(waypoint_tokens_out if waypoint_tokens_known else None),
        waypoint_cached_tokens_in=(
            waypoint_cached_tokens_in if waypoint_cached_tokens_known else None
        ),
        project_cost_usd=project_cost_usd,
        project_tokens_in=project_tokens_in,
        project_tokens_out=project_tokens_out,
        project_cached_tokens_in=project_cached_tokens_in,
        tokens_known=waypoint_tokens_known,
        cached_tokens_known=waypoint_cached_tokens_known,
    )
    return (
        f"{role}:metrics_updated",
        {"role": role, "metrics": metrics.to_metadata()},
    )


def apply_stream_complete_metrics(
    *,
    loop_state: _LoopState,
    chunk: StreamComplete,
    waypoint_id: str,
    role: str,
    max_iterations: int,
    metrics_collector: "MetricsCollector | MetricsCollectorLike | None",
    report_progress: object,
) -> float | None:
    """Update loop-state metrics from StreamComplete and emit metrics progress."""
    loop_state.iteration_tokens_in = chunk.tokens_in
    loop_state.iteration_tokens_out = chunk.tokens_out
    loop_state.iteration_cached_tokens_in = chunk.cached_tokens_in
    if chunk.cost_usd is not None:
        loop_state.waypoint_cost_usd += chunk.cost_usd
    if chunk.tokens_in is not None:
        loop_state.waypoint_tokens_in += chunk.tokens_in
        loop_state.waypoint_tokens_known = True
    if chunk.tokens_out is not None:
        loop_state.waypoint_tokens_out += chunk.tokens_out
        loop_state.waypoint_tokens_known = True
    if chunk.cached_tokens_in is not None:
        loop_state.waypoint_cached_tokens_in += chunk.cached_tokens_in
        loop_state.waypoint_cached_tokens_known = True
    output, metadata = build_metrics_progress_payload(
        role=role,
        waypoint_id=waypoint_id,
        delta_cost_usd=chunk.cost_usd,
        delta_tokens_in=chunk.tokens_in,
        delta_tokens_out=chunk.tokens_out,
        delta_cached_tokens_in=chunk.cached_tokens_in,
        waypoint_cost_usd=loop_state.waypoint_cost_usd,
        waypoint_tokens_in=loop_state.waypoint_tokens_in,
        waypoint_tokens_out=loop_state.waypoint_tokens_out,
        waypoint_cached_tokens_in=loop_state.waypoint_cached_tokens_in,
        waypoint_tokens_known=loop_state.waypoint_tokens_known,
        waypoint_cached_tokens_known=loop_state.waypoint_cached_tokens_known,
        metrics_collector=metrics_collector,
    )
    if not callable(report_progress):
        return chunk.cost_usd
    report_progress(
        loop_state.iteration,
        max_iterations,
        "metrics_updated",
        output,
        metadata=metadata,
    )
    return chunk.cost_usd


def log_iteration_end_with_usage(
    *,
    log_writer: "ExecutionLogWriter",
    loop_state: _LoopState,
    iteration_cost: float | None,
) -> None:
    """Write iteration end entry including optional provider token usage."""
    log_writer.log_iteration_end(
        loop_state.iteration,
        iteration_cost,
        tokens_in=loop_state.iteration_tokens_in,
        tokens_out=loop_state.iteration_tokens_out,
        cached_tokens_in=loop_state.iteration_cached_tokens_in,
    )


def persist_waypoint_memory(
    *,
    project_path: Path,
    waypoint: Waypoint,
    max_iterations: int,
    result: str,
    iteration: int,
    reported_validation_commands: list[str],
    captured_criteria: dict[int, CriterionVerification],
    tool_validation_evidence: dict[str, CapturedEvidence],
    protocol_derailments: list[str],
    workspace_summary: WorkspaceDiffSummary | None,
    error_summary: str | None,
    logger: logging.Logger,
) -> None:
    """Persist waypoint execution memory for future waypoint retrieval."""
    try:
        verified_criteria = sorted(
            idx
            for idx, criterion in captured_criteria.items()
            if criterion.status == "verified"
        )
        validation_commands = tuple(dict.fromkeys(reported_validation_commands))
        useful_commands = tuple(
            command
            for command in dict.fromkeys(
                [*reported_validation_commands, *tool_validation_evidence.keys()]
            )
            if command
        )
        changed_files: tuple[str, ...] = ()
        approx_tokens_changed: int | None = None
        if workspace_summary is not None:
            changed_files = tuple(
                item.path for item in workspace_summary.top_changed_files
            )
            approx_tokens_changed = workspace_summary.approx_tokens_changed

        record = WaypointMemoryRecord(
            schema_version="v1",
            saved_at_utc=datetime.now(UTC).isoformat(),
            waypoint_id=waypoint.id,
            title=waypoint.title,
            objective=waypoint.objective,
            dependencies=tuple(waypoint.dependencies),
            result=result,
            iterations_used=iteration,
            max_iterations=max_iterations,
            protocol_derailments=tuple(protocol_derailments[-8:]),
            error_summary=error_summary,
            changed_files=changed_files,
            approx_tokens_changed=approx_tokens_changed,
            validation_commands=validation_commands,
            useful_commands=useful_commands[:8],
            verified_criteria=tuple(verified_criteria),
        )
        save_waypoint_memory(project_path, record)
    except Exception:
        logger.exception("Failed to persist waypoint memory for %s", waypoint.id)


def validate_no_external_changes(
    project_path: Path,
    file_operations: Sequence[FileOperation],
) -> list[str]:
    """Check if any file operations targeted paths outside the project directory."""
    violations: list[str] = []
    project_root = project_path.resolve()

    for file_op in file_operations:
        if file_op.tool_name not in ("Edit", "Write", "Read"):
            continue
        if not file_op.file_path:
            continue
        try:
            candidate = Path(file_op.file_path)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (project_root / candidate).resolve()
            )
            if not resolved.is_relative_to(project_root):
                violations.append(str(file_op.file_path))
        except OSError:
            violations.append(str(file_op.file_path))

    return violations
