"""Deterministic tests for verify models and compare helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from waypoints.llm.client import StreamChunk
from waypoints.verify import compare as verify_compare
from waypoints.verify.models import (
    ComparisonResult,
    ComparisonVerdict,
    VerificationReport,
    VerificationStatus,
    VerificationStep,
)


def test_comparison_result_roundtrip_dict() -> None:
    result = ComparisonResult(
        verdict=ComparisonVerdict.EQUIVALENT,
        confidence=0.95,
        rationale="same intent",
        differences=[],
        artifact_type="spec",
        timestamp=datetime(2026, 2, 7, tzinfo=UTC),
    )

    data = result.to_dict()
    loaded = ComparisonResult.from_dict(data)

    assert loaded.verdict == ComparisonVerdict.EQUIVALENT
    assert loaded.confidence == 0.95
    assert loaded.artifact_type == "spec"
    assert loaded.timestamp == datetime(2026, 2, 7, tzinfo=UTC)


def test_verification_report_finalize_sets_pass_status() -> None:
    report = VerificationReport(
        genspec_path="/tmp/genspec",
        reference_path="/tmp/ref",
        overall_status=VerificationStatus.PARTIAL,
    )
    report.add_step(VerificationStep(name="spec", status="pass"))
    report.add_step(VerificationStep(name="plan", status="pass"))

    report.finalize()

    assert report.overall_status == VerificationStatus.PASS
    assert report.completed_at is not None


def test_verification_report_finalize_sets_error_status() -> None:
    report = VerificationReport(
        genspec_path="/tmp/genspec",
        reference_path="/tmp/ref",
        overall_status=VerificationStatus.PASS,
    )
    report.add_step(VerificationStep(name="spec", status="pass"))
    report.add_step(VerificationStep(name="plan", status="error"))

    report.finalize()

    assert report.overall_status == VerificationStatus.ERROR


def test_parse_comparison_response_handles_markdown_and_invalid_json() -> None:
    wrapped = """```json
{"verdict":"equivalent","confidence":1.0,"rationale":"same","differences":[]}
```"""
    parsed = verify_compare._parse_comparison_response(wrapped)
    assert parsed["verdict"] == "equivalent"

    invalid = verify_compare._parse_comparison_response("not json")
    assert invalid["verdict"] == "uncertain"
    assert invalid["confidence"] == 0.0


def test_format_flight_plan_includes_waypoint_fields() -> None:
    text = verify_compare._format_flight_plan(
        {
            "waypoints": [
                {
                    "id": "WP-001",
                    "title": "Build API",
                    "objective": "Implement endpoint",
                    "acceptance_criteria": ["returns JSON"],
                    "parent_id": "EPIC-1",
                }
            ]
        }
    )
    assert "WP-001" in text
    assert "returns JSON" in text
    assert "**Parent:** EPIC-1" in text


class _FakeClient:
    def __init__(
        self,
        metrics_collector: Any | None = None,
        phase: str = "unknown",
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.phase = phase
        _ = (metrics_collector, provider, model)

    def stream_message(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        max_tokens: int = 4096,
    ) -> list[StreamChunk]:
        _ = (messages, system, max_tokens)
        return [
            StreamChunk(
                text=(
                    '{"verdict":"equivalent","confidence":0.9,'
                    '"rationale":"matches","differences":[]}'
                )
            )
        ]


def test_compare_specs_uses_llm_output(monkeypatch: Any) -> None:
    monkeypatch.setattr(verify_compare, "ChatClient", _FakeClient)

    result = verify_compare.compare_specs("# A", "# B")

    assert result.verdict == ComparisonVerdict.EQUIVALENT
    assert result.artifact_type == "spec"
    assert result.confidence == 0.9


def test_compare_flight_plans_uses_llm_output(monkeypatch: Any) -> None:
    monkeypatch.setattr(verify_compare, "ChatClient", _FakeClient)

    result = verify_compare.compare_flight_plans(
        {"waypoints": [{"id": "WP-1", "title": "A", "objective": "X"}]},
        {"waypoints": [{"id": "WP-1", "title": "B", "objective": "X"}]},
    )

    assert result.verdict == ComparisonVerdict.EQUIVALENT
    assert result.artifact_type == "plan"
