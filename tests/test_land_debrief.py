"""Tests for land debrief formatting helpers."""

from waypoints.tui.screens.land import _format_token_summary


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
