"""Tests for land debrief formatting and analysis helpers."""

from waypoints.fly.execution_log import ExecutionEntry, ExecutionLog
from waypoints.orchestration.debrief import DebriefService, _format_token_summary


def test_format_token_summary_returns_none_when_empty() -> None:
    """No summary for zero token counts."""
    assert _format_token_summary(0, 0) is None


def test_format_token_summary_formats_counts() -> None:
    """Summary includes formatted token counts."""
    summary = _format_token_summary(1234, 5678)
    assert summary == "Total tokens were 1,234 in / 5,678 out."


def test_format_token_summary_formats_zero_in_or_out() -> None:
    """Zero counts should still render when the other side is non-zero."""
    assert _format_token_summary(0, 42) == "Total tokens were 0 in / 42 out."
    assert _format_token_summary(42, 0) == "Total tokens were 42 in / 0 out."


# ─── Contiguous Iteration Counting ───────────────────────────────────


def _make_log_with_iterations(iterations: list[int]) -> ExecutionLog:
    """Build an ExecutionLog with iteration_start entries."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    for it in iterations:
        log.entries.append(
            ExecutionEntry(
                entry_type="iteration_start",
                content=f"Iteration {it}",
                iteration=it,
            )
        )
    return log


def test_count_contiguous_sequential() -> None:
    """Sequential iterations 1..5 → 5."""
    log = _make_log_with_iterations([1, 2, 3, 4, 5])
    assert DebriefService._count_contiguous_iterations(log) == 5


def test_count_contiguous_gap_in_sequence() -> None:
    """Gap at iteration 3 (1, 2, 4, 5) → 2."""
    log = _make_log_with_iterations([1, 2, 4, 5])
    assert DebriefService._count_contiguous_iterations(log) == 2


def test_count_contiguous_empty_log() -> None:
    """Empty log → 0."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    assert DebriefService._count_contiguous_iterations(log) == 0


def test_count_contiguous_no_iteration_entries() -> None:
    """Log with entries but no iteration_start → 0."""
    log = ExecutionLog(waypoint_id="WP-TEST", waypoint_title="Test")
    log.entries.append(
        ExecutionEntry(entry_type="output", content="some output", iteration=1)
    )
    assert DebriefService._count_contiguous_iterations(log) == 0
