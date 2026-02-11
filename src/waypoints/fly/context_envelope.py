"""Prompt context envelope budgeting for role turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from waypoints.fly.protocol import PROTOCOL_SCHEMA_VERSION, FlyRole


@dataclass(frozen=True, slots=True)
class ContextSlice:
    """One bounded context section included in a prompt."""

    name: str
    source_ref: str
    original_chars: int
    used_chars: int
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source_ref": self.source_ref,
            "original_chars": self.original_chars,
            "used_chars": self.used_chars,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class ContextEnvelope:
    """Budget report for context injected into an agent turn."""

    waypoint_id: str
    role: FlyRole
    prompt_budget_chars: int
    tool_output_budget_chars: int
    slices: tuple[ContextSlice, ...]
    overflowed: bool
    schema_version: str = PROTOCOL_SCHEMA_VERSION
    artifact_id: str = field(default_factory=lambda: str(uuid4()))
    produced_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    artifact_type: Literal["context_envelope"] = "context_envelope"

    def __post_init__(self) -> None:
        if self.produced_at.tzinfo is None:
            object.__setattr__(
                self, "produced_at", self.produced_at.replace(tzinfo=UTC)
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "waypoint_id": self.waypoint_id,
            "produced_by_role": self.role.value,
            "produced_at": self.produced_at.isoformat(),
            "source_refs": [slice_.source_ref for slice_ in self.slices],
            "prompt_budget_chars": self.prompt_budget_chars,
            "tool_output_budget_chars": self.tool_output_budget_chars,
            "overflowed": self.overflowed,
            "slices": [slice_.to_dict() for slice_ in self.slices],
        }


def apply_context_envelope(
    *,
    waypoint_id: str,
    role: FlyRole,
    prompt_budget_chars: int,
    tool_output_budget_chars: int,
    directory_policy_context: str | None,
    waypoint_memory_context: str | None,
) -> tuple[ContextEnvelope, str | None, str | None]:
    """Apply deterministic truncation budgets to prompt context sections."""
    remaining = max(prompt_budget_chars, 0)
    slices: list[ContextSlice] = []

    policy_text, policy_slice, remaining = _clip_slice(
        name="directory_policy_context",
        source_ref="project-memory-index",
        text=directory_policy_context,
        remaining_budget=remaining,
    )
    slices.append(policy_slice)

    memory_text, memory_slice, remaining = _clip_slice(
        name="waypoint_memory_context",
        source_ref="waypoint-memory-index",
        text=waypoint_memory_context,
        remaining_budget=remaining,
    )
    slices.append(memory_slice)

    overflowed = any(slice_.truncated for slice_ in slices)
    envelope = ContextEnvelope(
        waypoint_id=waypoint_id,
        role=role,
        prompt_budget_chars=max(prompt_budget_chars, 0),
        tool_output_budget_chars=max(tool_output_budget_chars, 0),
        slices=tuple(slices),
        overflowed=overflowed,
    )
    return envelope, policy_text, memory_text


def clip_tool_output_for_context(output: str | None, max_chars: int) -> str | None:
    """Bound tool output retained in executor state for later reinjection."""
    if output is None:
        return None
    if max_chars <= 0:
        return None
    if len(output) <= max_chars:
        return output
    return output[: max_chars - 13] + "\n... (clipped)"


def _clip_slice(
    *,
    name: str,
    source_ref: str,
    text: str | None,
    remaining_budget: int,
) -> tuple[str | None, ContextSlice, int]:
    source_text = text or ""
    original_chars = len(source_text)
    if not source_text or remaining_budget <= 0:
        return (
            None,
            ContextSlice(
                name=name,
                source_ref=source_ref,
                original_chars=original_chars,
                used_chars=0,
                truncated=bool(source_text),
            ),
            remaining_budget,
        )

    if original_chars <= remaining_budget:
        return (
            source_text,
            ContextSlice(
                name=name,
                source_ref=source_ref,
                original_chars=original_chars,
                used_chars=original_chars,
                truncated=False,
            ),
            remaining_budget - original_chars,
        )

    clipped = source_text[:remaining_budget]
    return (
        clipped,
        ContextSlice(
            name=name,
            source_ref=source_ref,
            original_chars=original_chars,
            used_chars=remaining_budget,
            truncated=True,
        ),
        0,
    )
