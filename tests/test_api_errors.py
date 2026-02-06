from __future__ import annotations

from datetime import UTC, datetime

from waypoints.llm.providers.base import extract_reset_datetime


def test_extract_reset_datetime_relative() -> None:
    now = datetime(2026, 2, 6, 12, 0, tzinfo=UTC)

    result = extract_reset_datetime("usage limit exceeded; resets in 2 hours", now=now)

    assert result == datetime(2026, 2, 6, 14, 0, tzinfo=UTC)


def test_extract_reset_datetime_with_timezone() -> None:
    now = datetime(2026, 2, 6, 20, 0, tzinfo=UTC)

    result = extract_reset_datetime(
        "out of extra usage; resets 7pm (America/New_York)",
        now=now,
    )

    assert result == datetime(2026, 2, 7, 0, 0, tzinfo=UTC)


def test_extract_reset_datetime_returns_none_when_missing() -> None:
    assert extract_reset_datetime("quota exceeded without reset info") is None
