"""Tests for executor error classification policy."""

from __future__ import annotations

from waypoints.fly.intervention import InterventionType
from waypoints.fly.intervention_policy import classify_execution_error
from waypoints.llm.metrics import BudgetExceededError


def test_classify_execution_error_rate_limited() -> None:
    result = classify_execution_error(
        RuntimeError("429 Too Many Requests: rate limit exceeded"),
        full_output="",
    )

    assert result.intervention_type == InterventionType.RATE_LIMITED
    assert result.api_error_type.value == "rate_limited"
    assert "rate limit" in result.error_summary.lower()


def test_classify_execution_error_from_output_fallback() -> None:
    result = classify_execution_error(
        RuntimeError("opaque runtime failure"),
        full_output="provider overloaded - 503 service unavailable",
    )

    assert result.intervention_type == InterventionType.API_UNAVAILABLE
    assert result.api_error_type.value == "api_unavailable"


def test_classify_execution_error_budget_exception() -> None:
    result = classify_execution_error(
        BudgetExceededError("cost", current_value=11.0, limit_value=10.0),
        full_output="",
    )

    assert result.intervention_type == InterventionType.BUDGET_EXCEEDED
    assert result.api_error_type.value == "budget_exceeded"
    assert "configured budget $10.00 reached (current $11.00)." in (
        result.error_summary.lower()
    )


def test_classify_execution_error_extracts_budget_reset_time() -> None:
    result = classify_execution_error(
        RuntimeError("You are out of extra usage"),
        full_output="usage limit reached; resets 7pm (America/New_York)",
    )

    assert result.intervention_type == InterventionType.BUDGET_EXCEEDED
    assert result.reset_at is not None
    assert result.reset_at.utcoffset() is not None
