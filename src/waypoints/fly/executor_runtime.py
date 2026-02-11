"""Non-orchestrator runtime helpers for waypoint execution."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

from waypoints.fly.evidence import FileOperation
from waypoints.fly.provenance import WorkspaceDiffSummary
from waypoints.git.receipt import CapturedEvidence, CriterionVerification
from waypoints.memory import WaypointMemoryRecord, save_waypoint_memory
from waypoints.models.waypoint import Waypoint


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
