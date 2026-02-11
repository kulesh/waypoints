"""Tests for FLY multi-agent protocol artifacts."""

from __future__ import annotations

import json

import pytest

from waypoints.fly.protocol import (
    BuildArtifact,
    BuildCommandRecord,
    BuildPlan,
    ClarificationRequest,
    CriterionVerdict,
    FlyRole,
    GuidancePacket,
    OrchestratorDecision,
    ProtocolArtifact,
    VerificationCriterionResult,
    VerificationReport,
    artifact_from_dict,
    artifact_from_json,
    artifact_to_json,
)


def test_guidance_packet_round_trip_json() -> None:
    packet = GuidancePacket(
        waypoint_id="WP-101",
        produced_by_role=FlyRole.ORCHESTRATOR,
        source_refs=("docs/adr/0004-multi-agent-fly-handoff-protocol.md",),
        covenant_version="2026-02-11",
        policy_hash="abc123",
        role_constraints=("Do not mutate receipts",),
        stop_conditions=("Ask clarification on ambiguity",),
        attached_skills=("python-pytest-ruff",),
    )

    serialized = artifact_to_json(packet)
    restored = artifact_from_json(serialized)

    assert isinstance(restored, GuidancePacket)
    assert restored.waypoint_id == "WP-101"
    assert restored.covenant_version == "2026-02-11"
    assert restored.policy_hash == "abc123"
    assert restored.role_constraints == ("Do not mutate receipts",)


def test_build_plan_from_dict_tolerates_unknown_fields() -> None:
    raw = {
        "artifact_type": "build_plan",
        "schema_version": "1.0",
        "artifact_id": "artifact-1",
        "waypoint_id": "WP-202",
        "produced_by_role": "builder",
        "produced_at": "2026-02-11T01:02:03+00:00",
        "source_refs": ["src/a.py"],
        "intended_files": ["src/a.py", "tests/test_a.py"],
        "validation_plan": ["uv run pytest tests/test_a.py"],
        "criterion_coverage_map": {"0": "add API", "1": "add tests"},
        "budget_envelope": {"prompt_tokens": 5000, "tool_output_chars": 24000},
        "unexpected_additive_field": {"future": True},
    }

    artifact = artifact_from_dict(raw)

    assert isinstance(artifact, BuildPlan)
    assert artifact.criterion_coverage_map == {0: "add API", 1: "add tests"}
    assert artifact.budget_envelope["prompt_tokens"] == 5000


def test_verification_report_helpers() -> None:
    report = VerificationReport(
        waypoint_id="WP-303",
        produced_by_role=FlyRole.VERIFIER,
        criteria_results=(
            VerificationCriterionResult(
                index=0,
                verdict=CriterionVerdict.PASS,
                evidence_refs=("receipts/wp-303.json",),
            ),
            VerificationCriterionResult(
                index=1,
                verdict=CriterionVerdict.INCONCLUSIVE,
                evidence_refs=("logs/run-1",),
            ),
        ),
    )

    assert report.all_passed is False
    assert report.has_failures is False
    assert report.has_inconclusive is True


def test_artifact_from_dict_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unsupported artifact_type"):
        artifact_from_dict(
            {
                "artifact_type": "future_custom",
                "schema_version": "1.0",
                "artifact_id": "a-1",
                "waypoint_id": "WP-1",
                "produced_by_role": "orchestrator",
                "produced_at": "2026-02-11T00:00:00+00:00",
                "source_refs": [],
            }
        )


def test_build_artifact_serialization_includes_command_ledger() -> None:
    artifact = BuildArtifact(
        waypoint_id="WP-404",
        produced_by_role=FlyRole.BUILDER,
        touched_files=("src/main.py",),
        diff_summary="Added API endpoint",
        command_ledger=(
            BuildCommandRecord(
                command="uv run pytest tests/test_main.py",
                exit_code=0,
                evidence_ref="receipts/wp-404.json",
            ),
        ),
        criterion_coverage_claims={0: "endpoint implemented"},
        completion_marker_payload="<waypoint-complete>WP-404</waypoint-complete>",
    )

    encoded = artifact.to_dict()
    roundtrip = BuildArtifact.from_dict(encoded)

    assert roundtrip.command_ledger[0].command == "uv run pytest tests/test_main.py"
    assert roundtrip.command_ledger[0].exit_code == 0
    assert roundtrip.criterion_coverage_claims == {0: "endpoint implemented"}


def test_protocol_artifact_json_is_object() -> None:
    text = json.dumps([{"artifact_type": "guidance_packet"}])
    with pytest.raises(ValueError, match="must decode to an object"):
        artifact_from_json(text)


def test_clarification_request_round_trip() -> None:
    request = ClarificationRequest(
        waypoint_id="WP-505",
        produced_by_role=FlyRole.BUILDER,
        blocking_question="Spec says two different timeout values. Which one wins?",
        decision_context="docs/product-spec.md timeout section conflicts with README",
        confidence_level=0.34,
        requested_options=("Use spec timeout", "Use README timeout"),
    )

    restored = artifact_from_dict(request.to_dict())

    assert isinstance(restored, ProtocolArtifact)
    assert isinstance(restored, ClarificationRequest)
    assert restored.confidence_level == pytest.approx(0.34)


def test_orchestrator_decision_round_trip() -> None:
    decision = OrchestratorDecision(
        waypoint_id="WP-606",
        produced_by_role=FlyRole.ORCHESTRATOR,
        referenced_artifact_ids=("build-1", "verify-1"),
        reason_code="verification_passed",
        status_mutation="complete",
    )

    parsed = OrchestratorDecision.from_dict(decision.to_dict())

    assert parsed.reason_code == "verification_passed"
    assert parsed.referenced_artifact_ids == ("build-1", "verify-1")
