"""Structured execution protocol reporting for FLY phase."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import uuid4

PROTOCOL_SCHEMA_VERSION = "1.0"


class ExecutionStage(str, Enum):
    """Ordered execution stages for waypoint completion."""

    ANALYZE = "analyze"
    PLAN = "plan"
    TEST = "test"
    CODE = "code"
    RUN = "run"
    FIX = "fix"
    LINT = "lint"
    REPORT = "report"


@dataclass(frozen=True)
class StageReport:
    """Structured report for a single execution stage."""

    stage: ExecutionStage
    success: bool
    output: str
    artifacts: list[str]
    next_stage: ExecutionStage | None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StageReport":
        stage_value = data.get("stage")
        if stage_value is None:
            raise ValueError("Stage report missing 'stage'")
        stage = ExecutionStage(str(stage_value))

        next_stage_value = data.get("next_stage")
        next_stage = ExecutionStage(str(next_stage_value)) if next_stage_value else None

        return cls(
            stage=stage,
            success=bool(data.get("success")),
            output=str(data.get("output", "")),
            artifacts=[str(item) for item in data.get("artifacts", [])],
            next_stage=next_stage,
        )


STAGE_REPORT_PATTERN = re.compile(
    r"<execution-stage>\s*(\{.*?\})\s*</execution-stage>",
    re.DOTALL,
)


def parse_stage_reports(text: str) -> list[StageReport]:
    """Parse structured stage reports from model output."""
    reports: list[StageReport] = []
    for match in STAGE_REPORT_PATTERN.findall(text):
        try:
            data = json.loads(match)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            reports.append(StageReport.from_dict(data))
        except (ValueError, KeyError):
            continue
    return reports


class FlyRole(str, Enum):
    """Role identifiers for multi-agent FLY orchestration."""

    ORCHESTRATOR = "orchestrator"
    BUILDER = "builder"
    VERIFIER = "verifier"
    REPAIR = "repair"


class CriterionVerdict(str, Enum):
    """Per-criterion verifier verdict."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class DecisionDisposition(str, Enum):
    """Orchestrator decision outcomes."""

    ACCEPT = "accept"
    REWORK = "rework"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class ProtocolArtifact:
    """Base metadata contract for all control-plane protocol artifacts."""

    waypoint_id: str
    produced_by_role: FlyRole
    source_refs: tuple[str, ...] = ()
    schema_version: str = PROTOCOL_SCHEMA_VERSION
    artifact_id: str = field(default_factory=lambda: str(uuid4()))
    produced_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    artifact_type: ClassVar[str] = "protocol_artifact"

    def _metadata_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "waypoint_id": self.waypoint_id,
            "produced_by_role": self.produced_by_role.value,
            "produced_at": self.produced_at.isoformat(),
            "source_refs": list(self.source_refs),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize artifact metadata (subclasses should extend)."""
        return self._metadata_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProtocolArtifact":
        """Deserialize artifact from dictionary."""
        raise NotImplementedError(f"{cls.__name__}.from_dict must be implemented")

    @classmethod
    def _parse_metadata(cls, data: Mapping[str, Any]) -> dict[str, Any]:
        raw_waypoint_id = data.get("waypoint_id")
        if not isinstance(raw_waypoint_id, str) or not raw_waypoint_id.strip():
            raise ValueError("Artifact missing waypoint_id")

        raw_role = data.get("produced_by_role")
        if raw_role is None:
            raise ValueError("Artifact missing produced_by_role")
        role = FlyRole(str(raw_role))

        raw_artifact_id = data.get("artifact_id")
        if not isinstance(raw_artifact_id, str) or not raw_artifact_id.strip():
            raise ValueError("Artifact missing artifact_id")

        raw_schema = data.get("schema_version")
        if not isinstance(raw_schema, str) or not raw_schema.strip():
            raise ValueError("Artifact missing schema_version")

        raw_produced_at = data.get("produced_at")
        if not isinstance(raw_produced_at, str) or not raw_produced_at.strip():
            raise ValueError("Artifact missing produced_at")
        produced_at = datetime.fromisoformat(raw_produced_at)

        source_refs = _as_tuple_of_str(data.get("source_refs"))

        return {
            "waypoint_id": raw_waypoint_id,
            "produced_by_role": role,
            "source_refs": source_refs,
            "schema_version": raw_schema,
            "artifact_id": raw_artifact_id,
            "produced_at": produced_at,
        }


@dataclass(frozen=True, slots=True)
class GuidancePacket(ProtocolArtifact):
    """Turn-scoped policy projection attached to each role execution."""

    covenant_version: str = ""
    policy_hash: str = ""
    role_constraints: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    attached_skills: tuple[str, ...] = ()

    artifact_type: ClassVar[str] = "guidance_packet"

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "covenant_version": self.covenant_version,
                "policy_hash": self.policy_hash,
                "role_constraints": list(self.role_constraints),
                "stop_conditions": list(self.stop_conditions),
                "attached_skills": list(self.attached_skills),
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GuidancePacket":
        return cls(
            **cls._parse_metadata(data),
            covenant_version=str(data.get("covenant_version", "")),
            policy_hash=str(data.get("policy_hash", "")),
            role_constraints=_as_tuple_of_str(data.get("role_constraints")),
            stop_conditions=_as_tuple_of_str(data.get("stop_conditions")),
            attached_skills=_as_tuple_of_str(data.get("attached_skills")),
        )


@dataclass(frozen=True, slots=True)
class BuildPlan(ProtocolArtifact):
    """Builder's pre-execution implementation and validation plan."""

    intended_files: tuple[str, ...] = ()
    validation_plan: tuple[str, ...] = ()
    criterion_coverage_map: dict[int, str] = field(default_factory=dict)
    budget_envelope: dict[str, int] = field(default_factory=dict)

    artifact_type: ClassVar[str] = "build_plan"

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "intended_files": list(self.intended_files),
                "validation_plan": list(self.validation_plan),
                "criterion_coverage_map": {
                    str(index): note
                    for index, note in sorted(self.criterion_coverage_map.items())
                },
                "budget_envelope": dict(self.budget_envelope),
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BuildPlan":
        coverage_map = _as_int_keyed_map(data.get("criterion_coverage_map"))
        budget_map = _as_int_value_map(data.get("budget_envelope"))
        return cls(
            **cls._parse_metadata(data),
            intended_files=_as_tuple_of_str(data.get("intended_files")),
            validation_plan=_as_tuple_of_str(data.get("validation_plan")),
            criterion_coverage_map=coverage_map,
            budget_envelope=budget_map,
        )


@dataclass(frozen=True, slots=True)
class BuildCommandRecord:
    """One command run by builder during implementation."""

    command: str
    exit_code: int
    evidence_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BuildCommandRecord":
        command = str(data.get("command", ""))
        raw_exit_code = data.get("exit_code", 0)
        try:
            exit_code = int(raw_exit_code)
        except (TypeError, ValueError):
            exit_code = 0
        raw_ref = data.get("evidence_ref")
        return cls(
            command=command,
            exit_code=exit_code,
            evidence_ref=str(raw_ref) if raw_ref is not None else None,
        )


@dataclass(frozen=True, slots=True)
class BuildArtifact(ProtocolArtifact):
    """Builder's execution evidence and completion claim payload."""

    touched_files: tuple[str, ...] = ()
    diff_summary: str = ""
    command_ledger: tuple[BuildCommandRecord, ...] = ()
    criterion_coverage_claims: dict[int, str] = field(default_factory=dict)
    completion_marker_payload: str | None = None

    artifact_type: ClassVar[str] = "build_artifact"

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "touched_files": list(self.touched_files),
                "diff_summary": self.diff_summary,
                "command_ledger": [entry.to_dict() for entry in self.command_ledger],
                "criterion_coverage_claims": {
                    str(index): note
                    for index, note in sorted(self.criterion_coverage_claims.items())
                },
                "completion_marker_payload": self.completion_marker_payload,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BuildArtifact":
        raw_ledger = data.get("command_ledger", [])
        ledger: list[BuildCommandRecord] = []
        if isinstance(raw_ledger, Sequence) and not isinstance(raw_ledger, str):
            for entry in raw_ledger:
                if isinstance(entry, Mapping):
                    ledger.append(BuildCommandRecord.from_dict(entry))

        completion_payload = data.get("completion_marker_payload")
        return cls(
            **cls._parse_metadata(data),
            touched_files=_as_tuple_of_str(data.get("touched_files")),
            diff_summary=str(data.get("diff_summary", "")),
            command_ledger=tuple(ledger),
            criterion_coverage_claims=_as_int_keyed_map(
                data.get("criterion_coverage_claims")
            ),
            completion_marker_payload=(
                str(completion_payload) if completion_payload is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class VerificationRequest(ProtocolArtifact):
    """Verifier input contract for criterion-by-criterion review."""

    criteria_under_review: tuple[str, ...] = ()
    expected_evidence_refs: tuple[str, ...] = ()
    policy_constraints: tuple[str, ...] = ()

    artifact_type: ClassVar[str] = "verification_request"

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "criteria_under_review": list(self.criteria_under_review),
                "expected_evidence_refs": list(self.expected_evidence_refs),
                "policy_constraints": list(self.policy_constraints),
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VerificationRequest":
        return cls(
            **cls._parse_metadata(data),
            criteria_under_review=_as_tuple_of_str(data.get("criteria_under_review")),
            expected_evidence_refs=_as_tuple_of_str(data.get("expected_evidence_refs")),
            policy_constraints=_as_tuple_of_str(data.get("policy_constraints")),
        )


@dataclass(frozen=True, slots=True)
class VerificationCriterionResult:
    """Verifier verdict and evidence for one criterion."""

    index: int
    verdict: CriterionVerdict
    evidence_refs: tuple[str, ...] = ()
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "verdict": self.verdict.value,
            "evidence_refs": list(self.evidence_refs),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VerificationCriterionResult":
        raw_index = data.get("index", 0)
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = 0
        verdict = CriterionVerdict(str(data.get("verdict", "inconclusive")))
        raw_note = data.get("note")
        return cls(
            index=index,
            verdict=verdict,
            evidence_refs=_as_tuple_of_str(data.get("evidence_refs")),
            note=str(raw_note) if raw_note is not None else None,
        )


@dataclass(frozen=True, slots=True)
class VerificationReport(ProtocolArtifact):
    """Verifier output contract used by orchestrator decisioning."""

    criteria_results: tuple[VerificationCriterionResult, ...] = ()
    unresolved_doubts: tuple[str, ...] = ()
    receipt_path: str | None = None

    artifact_type: ClassVar[str] = "verification_report"

    @property
    def has_failures(self) -> bool:
        return any(
            result.verdict == CriterionVerdict.FAIL for result in self.criteria_results
        )

    @property
    def has_inconclusive(self) -> bool:
        return any(
            result.verdict == CriterionVerdict.INCONCLUSIVE
            for result in self.criteria_results
        )

    @property
    def all_passed(self) -> bool:
        if not self.criteria_results:
            return False
        return all(
            result.verdict == CriterionVerdict.PASS for result in self.criteria_results
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "criteria_results": [
                    result.to_dict() for result in self.criteria_results
                ],
                "unresolved_doubts": list(self.unresolved_doubts),
                "receipt_path": self.receipt_path,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VerificationReport":
        raw_results = data.get("criteria_results", [])
        criteria_results: list[VerificationCriterionResult] = []
        if isinstance(raw_results, Sequence) and not isinstance(raw_results, str):
            for item in raw_results:
                if isinstance(item, Mapping):
                    criteria_results.append(VerificationCriterionResult.from_dict(item))

        raw_receipt_path = data.get("receipt_path")
        return cls(
            **cls._parse_metadata(data),
            criteria_results=tuple(criteria_results),
            unresolved_doubts=_as_tuple_of_str(data.get("unresolved_doubts")),
            receipt_path=(
                str(raw_receipt_path) if raw_receipt_path is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ClarificationRequest(ProtocolArtifact):
    """Role-emitted question for ambiguous policy/intent decisions."""

    blocking_question: str = ""
    decision_context: str = ""
    confidence_level: float = 0.0
    requested_options: tuple[str, ...] = ()

    artifact_type: ClassVar[str] = "clarification_request"

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "blocking_question": self.blocking_question,
                "decision_context": self.decision_context,
                "confidence_level": self.confidence_level,
                "requested_options": list(self.requested_options),
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClarificationRequest":
        raw_confidence = data.get("confidence_level", 0.0)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            **cls._parse_metadata(data),
            blocking_question=str(data.get("blocking_question", "")),
            decision_context=str(data.get("decision_context", "")),
            confidence_level=confidence,
            requested_options=_as_tuple_of_str(data.get("requested_options")),
        )


@dataclass(frozen=True, slots=True)
class ClarificationResponse(ProtocolArtifact):
    """Orchestrator response to a clarification request."""

    request_artifact_id: str = ""
    chosen_option: str = ""
    rationale: str = ""
    updated_constraints: tuple[str, ...] = ()

    artifact_type: ClassVar[str] = "clarification_response"

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "request_artifact_id": self.request_artifact_id,
                "chosen_option": self.chosen_option,
                "rationale": self.rationale,
                "updated_constraints": list(self.updated_constraints),
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ClarificationResponse":
        return cls(
            **cls._parse_metadata(data),
            request_artifact_id=str(data.get("request_artifact_id", "")),
            chosen_option=str(data.get("chosen_option", "")),
            rationale=str(data.get("rationale", "")),
            updated_constraints=_as_tuple_of_str(data.get("updated_constraints")),
        )


@dataclass(frozen=True, slots=True)
class OrchestratorDecision(ProtocolArtifact):
    """Final orchestrator disposition over builder/verifier artifacts."""

    disposition: DecisionDisposition = DecisionDisposition.ESCALATE
    reason_code: str = ""
    referenced_artifact_ids: tuple[str, ...] = ()
    status_mutation: str | None = None

    artifact_type: ClassVar[str] = "orchestrator_decision"

    def to_dict(self) -> dict[str, Any]:
        payload = self._metadata_dict()
        payload.update(
            {
                "disposition": self.disposition.value,
                "reason_code": self.reason_code,
                "referenced_artifact_ids": list(self.referenced_artifact_ids),
                "status_mutation": self.status_mutation,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OrchestratorDecision":
        raw_disposition = data.get("disposition", DecisionDisposition.ESCALATE.value)
        raw_status_mutation = data.get("status_mutation")
        return cls(
            **cls._parse_metadata(data),
            disposition=DecisionDisposition(str(raw_disposition)),
            reason_code=str(data.get("reason_code", "")),
            referenced_artifact_ids=_as_tuple_of_str(
                data.get("referenced_artifact_ids")
            ),
            status_mutation=(
                str(raw_status_mutation) if raw_status_mutation is not None else None
            ),
        )


def _as_tuple_of_str(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        value = raw.strip()
        return (value,) if value else ()
    if isinstance(raw, Sequence):
        return tuple(str(item) for item in raw if str(item).strip())
    return ()


def _as_int_keyed_map(raw: Any) -> dict[int, str]:
    if not isinstance(raw, Mapping):
        return {}

    result: dict[int, str] = {}
    for key, value in raw.items():
        try:
            index = int(str(key))
        except (TypeError, ValueError):
            continue
        result[index] = str(value)
    return result


def _as_int_value_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}

    result: dict[str, int] = {}
    for key, value in raw.items():
        key_text = str(key)
        try:
            result[key_text] = int(value)
        except (TypeError, ValueError):
            continue
    return result


_ARTIFACT_REGISTRY: dict[str, type[ProtocolArtifact]] = {
    GuidancePacket.artifact_type: GuidancePacket,
    BuildPlan.artifact_type: BuildPlan,
    BuildArtifact.artifact_type: BuildArtifact,
    VerificationRequest.artifact_type: VerificationRequest,
    VerificationReport.artifact_type: VerificationReport,
    ClarificationRequest.artifact_type: ClarificationRequest,
    ClarificationResponse.artifact_type: ClarificationResponse,
    OrchestratorDecision.artifact_type: OrchestratorDecision,
}


def artifact_from_dict(data: Mapping[str, Any]) -> ProtocolArtifact:
    """Deserialize a protocol artifact based on its artifact_type."""
    artifact_type = str(data.get("artifact_type", "")).strip()
    if not artifact_type:
        raise ValueError("Artifact missing artifact_type")

    artifact_cls = _ARTIFACT_REGISTRY.get(artifact_type)
    if artifact_cls is None:
        raise ValueError(f"Unsupported artifact_type: {artifact_type}")

    return artifact_cls.from_dict(data)


def artifact_from_json(payload: str) -> ProtocolArtifact:
    """Deserialize a protocol artifact from JSON string."""
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Artifact JSON must decode to an object")
    return artifact_from_dict(data)


def artifact_to_json(artifact: ProtocolArtifact) -> str:
    """Serialize an artifact to compact JSON."""
    to_dict = getattr(artifact, "to_dict", None)
    if not callable(to_dict):
        raise ValueError("Artifact does not support to_dict serialization")
    return json.dumps(to_dict(), separators=(",", ":"), sort_keys=True)
