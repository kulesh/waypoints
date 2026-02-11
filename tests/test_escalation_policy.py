"""Tests for executor escalation/protocol decision policy."""

from __future__ import annotations

from waypoints.fly.escalation_policy import (
    build_escalation_decision,
    detect_protocol_issues,
)


def test_detect_protocol_issues_reports_all_signals() -> None:
    issues = detect_protocol_issues(
        iteration_output="Implementation is complete. **WP-1 COMPLETE**",
        completion_marker="<waypoint-complete>WP-1</waypoint-complete>",
        stage_reports_logged=0,
        scope_drift_detected=True,
        waypoint_id="WP-1",
    )

    assert "claimed completion without exact completion marker" in issues
    assert "missing structured stage report" in issues
    assert "attempted tool access to blocked project areas" in issues


def test_build_escalation_decision_sets_incomplete_criteria_reason() -> None:
    decision = build_escalation_decision(
        iteration_output="working on criterion 0",
        completion_marker="<waypoint-complete>WP-1</waypoint-complete>",
        stage_reports_logged=1,
        scope_drift_detected=False,
        current_derailment_streak=1,
        acceptance_criteria=["Criterion A", "Criterion B"],
        verified_criteria={0},
        waypoint_id="WP-1",
    )

    assert decision.protocol_issues == []
    assert decision.protocol_derailment_streak == 0
    assert decision.next_reason_code == "incomplete_criteria"
    assert "[1] Criterion B" in decision.next_reason_detail
    assert decision.should_escalate is False


def test_build_escalation_decision_escalates_on_repeated_derailment() -> None:
    decision = build_escalation_decision(
        iteration_output="Implementation is complete. **WP-1 COMPLETE**",
        completion_marker="<waypoint-complete>WP-1</waypoint-complete>",
        stage_reports_logged=0,
        scope_drift_detected=False,
        current_derailment_streak=1,
        acceptance_criteria=["Criterion A"],
        verified_criteria=set(),
        waypoint_id="WP-1",
    )

    assert decision.protocol_derailment_streak == 2
    assert decision.next_reason_code == "protocol_violation"
    assert decision.should_escalate is True
    assert decision.escalation_summary is not None
