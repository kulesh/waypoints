from __future__ import annotations

from waypoints.fly.context_envelope import (
    apply_context_envelope,
    clip_tool_output_for_context,
)
from waypoints.fly.protocol import FlyRole


def test_apply_context_envelope_without_truncation() -> None:
    envelope, policy, memory = apply_context_envelope(
        waypoint_id="WP-1",
        role=FlyRole.BUILDER,
        prompt_budget_chars=100,
        tool_output_budget_chars=50,
        directory_policy_context="policy",
        waypoint_memory_context="memory",
    )

    assert policy == "policy"
    assert memory == "memory"
    assert envelope.overflowed is False
    assert tuple(slice_.used_chars for slice_ in envelope.slices) == (6, 6)


def test_apply_context_envelope_truncates_deterministically() -> None:
    envelope, policy, memory = apply_context_envelope(
        waypoint_id="WP-2",
        role=FlyRole.BUILDER,
        prompt_budget_chars=8,
        tool_output_budget_chars=50,
        directory_policy_context="policy",
        waypoint_memory_context="memory",
    )

    assert policy == "policy"
    assert memory == "me"
    assert envelope.overflowed is True
    assert envelope.slices[1].truncated is True
    assert envelope.slices[1].used_chars == 2


def test_clip_tool_output_for_context() -> None:
    clipped = clip_tool_output_for_context("x" * 20, 10)
    assert clipped is not None
    assert clipped.endswith("... (clipped)")
    assert clip_tool_output_for_context("abc", 10) == "abc"
    assert clip_tool_output_for_context("abc", 0) is None
