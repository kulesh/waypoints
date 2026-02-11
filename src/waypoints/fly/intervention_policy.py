"""Classification policy for executor iteration errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from waypoints.fly.intervention import InterventionType
from waypoints.llm.client import extract_reset_datetime, extract_reset_time
from waypoints.llm.metrics import BudgetExceededError
from waypoints.llm.providers.base import (
    BUDGET_PATTERNS,
    RATE_LIMIT_PATTERNS,
    UNAVAILABLE_PATTERNS,
    APIErrorType,
    classify_api_error,
)

_USER_INTERVENTION_MARKERS = (
    "cannot proceed",
    "need human help",
    "blocked by",
    "unable to complete",
    "requires manual",
)


@dataclass(frozen=True, slots=True)
class ErrorClassification:
    """Structured classification of an executor iteration error."""

    api_error_type: APIErrorType
    intervention_type: InterventionType
    error_summary: str
    reset_at: datetime | None


def _infer_api_error_type(error: Exception, full_output: str) -> APIErrorType:
    """Classify error type, with output-based fallback when needed."""
    api_error_type = classify_api_error(error)
    if api_error_type != APIErrorType.UNKNOWN:
        return api_error_type

    lower_output = full_output.lower()
    for pattern in BUDGET_PATTERNS:
        if pattern in lower_output:
            return APIErrorType.BUDGET_EXCEEDED
    for pattern in RATE_LIMIT_PATTERNS:
        if pattern in lower_output:
            return APIErrorType.RATE_LIMITED
    for pattern in UNAVAILABLE_PATTERNS:
        if pattern in lower_output:
            return APIErrorType.API_UNAVAILABLE
    return APIErrorType.UNKNOWN


def _map_intervention_type(api_error_type: APIErrorType) -> InterventionType:
    return {
        APIErrorType.RATE_LIMITED: InterventionType.RATE_LIMITED,
        APIErrorType.API_UNAVAILABLE: InterventionType.API_UNAVAILABLE,
        APIErrorType.BUDGET_EXCEEDED: InterventionType.BUDGET_EXCEEDED,
    }.get(api_error_type, InterventionType.EXECUTION_ERROR)


def _base_summary(api_error_type: APIErrorType, error: Exception) -> str:
    error_summaries = {
        APIErrorType.RATE_LIMITED: (
            "Model provider rate limit reached. Wait a few minutes and retry."
        ),
        APIErrorType.API_UNAVAILABLE: (
            "Model provider temporarily unavailable. Try again shortly."
        ),
        APIErrorType.BUDGET_EXCEEDED: (
            "Model usage budget exceeded. Execution paused until budget resets."
        ),
    }
    return error_summaries.get(api_error_type, str(error))


def _budget_summary(error: Exception, full_output: str) -> str:
    if isinstance(error, BudgetExceededError):
        return (
            f"Configured budget ${error.limit_value:.2f} reached "
            f"(current ${error.current_value:.2f}). "
            "Execution paused until you increase the budget."
        )
    if (reset_time := extract_reset_time(str(error))) is not None:
        return f"Model usage budget exceeded. Resets {reset_time}."
    if (reset_time := extract_reset_time(full_output)) is not None:
        return f"Model usage budget exceeded. Resets {reset_time}."
    return "Model usage budget exceeded. Execution paused until budget resets."


def classify_execution_error(
    error: Exception, *, full_output: str
) -> ErrorClassification:
    """Classify execution error into intervention type and user summary."""
    api_error_type = _infer_api_error_type(error, full_output)
    intervention_type = _map_intervention_type(api_error_type)

    reset_at = extract_reset_datetime(str(error))
    if reset_at is None and full_output:
        reset_at = extract_reset_datetime(full_output)

    if api_error_type == APIErrorType.BUDGET_EXCEEDED:
        error_summary = _budget_summary(error, full_output)
    else:
        error_summary = _base_summary(api_error_type, error)

    return ErrorClassification(
        api_error_type=api_error_type,
        intervention_type=intervention_type,
        error_summary=error_summary,
        reset_at=reset_at,
    )


def needs_user_intervention(output: str) -> bool:
    """Check if output indicates explicit user intervention is needed."""
    lower_output = output.lower()
    return any(marker in lower_output for marker in _USER_INTERVENTION_MARKERS)


def extract_intervention_reason(output: str) -> str:
    """Extract contextual reason around an intervention marker."""
    lower_output = output.lower()
    for marker in _USER_INTERVENTION_MARKERS:
        if marker in lower_output:
            idx = lower_output.find(marker)
            start = max(0, idx - 100)
            end = min(len(output), idx + len(marker) + 200)
            context = output[start:end].strip()
            return f"Agent indicated: ...{context}..."
    return "Agent requested human intervention"
