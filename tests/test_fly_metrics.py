"""Tests for fly metrics formatting helpers."""

from waypoints.tui.screens.fly import _format_project_metrics


def test_format_project_metrics_includes_tokens_when_known() -> None:
    """Token counts show when tokens are known."""
    line = _format_project_metrics(1.23, 65, 100, 200, True, 50, True)
    assert "$1.23" in line
    assert "Tokens: 100 in / 200 out" in line
    assert "Cached: 50 in" in line


def test_format_project_metrics_shows_na_when_unknown() -> None:
    """Show tokens as n/a when counts are unknown but other stats exist."""
    line = _format_project_metrics(1.23, 65, 0, 0, False, None, False)
    assert "Tokens: n/a" in line


def test_format_project_metrics_omits_when_empty() -> None:
    """Empty inputs should return empty string."""
    assert _format_project_metrics(0.0, 0, None, None, False, None, False) == ""
