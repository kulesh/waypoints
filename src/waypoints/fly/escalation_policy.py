"""Policy helpers for protocol-drift detection and escalation decisions."""

from __future__ import annotations

from dataclasses import dataclass

COMPLETION_ALIAS_HINTS = (
    "waypoint_complete",
    "waypoint completed",
    "==completed==",
    "implementation is complete",
    "all waypoints",
)


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    """Result of evaluating iteration protocol and completion progress."""

    protocol_issues: list[str]
    protocol_derailment_streak: int
    next_reason_code: str
    next_reason_detail: str
    should_escalate: bool
    escalation_summary: str | None


def detect_protocol_issues(
    *,
    iteration_output: str,
    completion_marker: str,
    stage_reports_logged: int,
    scope_drift_detected: bool,
    waypoint_id: str,
) -> list[str]:
    """Detect recoverable protocol issues for iteration-to-iteration nudging."""
    issues: list[str] = []
    lower_output = iteration_output.lower()
    waypoint_alias = f"{waypoint_id.lower()} complete"
    claimed_complete = "complete" in lower_output and (
        any(hint in lower_output for hint in COMPLETION_ALIAS_HINTS)
        or waypoint_alias in lower_output
    )
    if claimed_complete and completion_marker not in iteration_output:
        issues.append("claimed completion without exact completion marker")
    if stage_reports_logged == 0:
        issues.append("missing structured stage report")
    if scope_drift_detected:
        issues.append("attempted tool access to blocked project areas")
    return issues


def build_escalation_decision(
    *,
    iteration_output: str,
    completion_marker: str,
    stage_reports_logged: int,
    scope_drift_detected: bool,
    current_derailment_streak: int,
    acceptance_criteria: list[str],
    verified_criteria: set[int],
    waypoint_id: str,
    max_derailment_streak: int = 2,
) -> EscalationDecision:
    """Determine next reason and escalation behavior for the next iteration."""
    protocol_issues = detect_protocol_issues(
        iteration_output=iteration_output,
        completion_marker=completion_marker,
        stage_reports_logged=stage_reports_logged,
        scope_drift_detected=scope_drift_detected,
        waypoint_id=waypoint_id,
    )

    if protocol_issues:
        escalation_issues = [
            issue
            for issue in protocol_issues
            if issue != "missing structured stage report"
        ]
        streak = current_derailment_streak + 1 if escalation_issues else 0
        next_reason_code = "protocol_violation"
        next_reason_detail = "; ".join(protocol_issues)
    else:
        unresolved_criteria = sorted(
            set(range(len(acceptance_criteria))) - verified_criteria
        )
        streak = 0
        if unresolved_criteria:
            remaining_labels = ", ".join(
                f"[{idx}] {acceptance_criteria[idx]}" for idx in unresolved_criteria
            )
            next_reason_code = "incomplete_criteria"
            next_reason_detail = (
                f"{len(unresolved_criteria)} criteria unresolved: {remaining_labels}"
            )
        else:
            next_reason_code = "validation_failure"
            next_reason_detail = (
                "Criteria appear complete, but completion protocol was not satisfied."
            )

    should_escalate = streak >= max_derailment_streak
    escalation_summary = (
        "Execution repeatedly violated waypoint protocol. "
        f"Issues: {next_reason_detail}"
        if should_escalate
        else None
    )

    return EscalationDecision(
        protocol_issues=protocol_issues,
        protocol_derailment_streak=streak,
        next_reason_code=next_reason_code,
        next_reason_detail=next_reason_detail,
        should_escalate=should_escalate,
        escalation_summary=escalation_summary,
    )
