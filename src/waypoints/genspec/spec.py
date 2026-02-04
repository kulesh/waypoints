"""Data models for Generative Specification format.

A Generative Specification captures all prompts, user decisions, and AI outputs
needed to reproduce a waypoints project. This enables shipping "recipes" instead
of compiled software - anyone can regenerate functionally equivalent software.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class Phase(Enum):
    """Phases in the waypoints generative journey."""

    SPARK = "spark"
    SHAPE_QA = "shape_qa"
    SHAPE_BRIEF = "shape_brief"
    SHAPE_SPEC = "shape_spec"
    CHART = "chart"
    CHART_BREAKDOWN = "chart_breakdown"
    CHART_ADD = "chart_add"
    FLY = "fly"


class OutputType(Enum):
    """Type of output from a generative step."""

    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"


class DecisionType(Enum):
    """Type of user decision."""

    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"


class ArtifactType(Enum):
    """Type of generated artifact."""

    IDEA_BRIEF = "idea_brief"
    PRODUCT_SPEC = "product_spec"
    FLIGHT_PLAN = "flight_plan"


class BundleFileType(Enum):
    """Type of file inside a genspec bundle."""

    GENSPEC = "genspec"
    ARTIFACT = "artifact"
    METADATA = "metadata"
    CHECKSUMS = "checksums"


@dataclass(frozen=True)
class BundleFile:
    """Descriptor for a file stored in a genspec bundle."""

    path: str
    file_type: BundleFileType
    artifact_type: ArtifactType | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "path": self.path,
            "type": self.file_type.value,
        }
        if self.artifact_type is not None:
            result["artifact_type"] = self.artifact_type.value
        return result


@dataclass(frozen=True)
class BundleMetadata:
    """Metadata for a genspec bundle."""

    schema: str
    version: str
    waypoints_version: str
    source_project: str
    created_at: datetime
    files: list[BundleFile]
    model: str | None = None
    model_version: str | None = None
    initial_idea: str | None = None
    genspec_path: str = "genspec.jsonl"
    checksums_path: str = "checksums.json"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "schema": self.schema,
            "version": self.version,
            "waypoints_version": self.waypoints_version,
            "source_project": self.source_project,
            "created_at": self.created_at.isoformat(),
            "genspec_path": self.genspec_path,
            "checksums_path": self.checksums_path,
            "files": [file.to_dict() for file in self.files],
        }
        if self.model:
            result["model"] = self.model
        if self.model_version:
            result["model_version"] = self.model_version
        if self.initial_idea:
            result["initial_idea"] = self.initial_idea
        return result


@dataclass(frozen=True)
class BundleChecksums:
    """Checksums for a genspec bundle."""

    algorithm: str
    files: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "algorithm": self.algorithm,
            "files": self.files,
        }


@dataclass
class StepInput:
    """Input for a generative step.

    Captures everything needed to reproduce the LLM call.
    """

    system_prompt: str | None = None
    user_prompt: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {}
        if self.system_prompt:
            result["system_prompt"] = self.system_prompt
        if self.user_prompt:
            result["user_prompt"] = self.user_prompt
        if self.messages:
            result["messages"] = self.messages
        if self.context:
            result["context"] = self.context
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepInput":
        """Create from dictionary."""
        return cls(
            system_prompt=data.get("system_prompt"),
            user_prompt=data.get("user_prompt", ""),
            messages=data.get("messages", []),
            context=data.get("context", {}),
        )


@dataclass
class StepOutput:
    """Output from a generative step.

    Stores the AI response for replay mode.
    """

    content: str
    output_type: OutputType = OutputType.TEXT
    parsed: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "content": self.content,
            "type": self.output_type.value,
        }
        if self.parsed is not None:
            result["parsed"] = self.parsed
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepOutput":
        """Create from dictionary."""
        return cls(
            content=data["content"],
            output_type=OutputType(data.get("type", "text")),
            parsed=data.get("parsed"),
        )


@dataclass
class StepMetadata:
    """Metadata about a generative step."""

    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {}
        if self.tokens_in is not None:
            result["tokens_in"] = self.tokens_in
        if self.tokens_out is not None:
            result["tokens_out"] = self.tokens_out
        if self.cost_usd is not None:
            result["cost_usd"] = self.cost_usd
        if self.latency_ms is not None:
            result["latency_ms"] = self.latency_ms
        if self.model is not None:
            result["model"] = self.model
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepMetadata":
        """Create from dictionary."""
        return cls(
            tokens_in=data.get("tokens_in"),
            tokens_out=data.get("tokens_out"),
            cost_usd=data.get("cost_usd"),
            latency_ms=data.get("latency_ms"),
            model=data.get("model"),
        )


@dataclass
class GenerativeStep:
    """A single generative step in the specification.

    Captures the prompt, AI response, and metadata for one generation.
    """

    step_id: str
    phase: Phase
    timestamp: datetime
    input: StepInput
    output: StepOutput
    metadata: StepMetadata = field(default_factory=StepMetadata)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "type": "step",
            "step_id": self.step_id,
            "phase": self.phase.value,
            "timestamp": self.timestamp.isoformat(),
            "input": self.input.to_dict(),
            "output": self.output.to_dict(),
        }
        metadata = self.metadata.to_dict()
        if metadata:
            result["metadata"] = metadata
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GenerativeStep":
        """Create from dictionary."""
        return cls(
            step_id=data["step_id"],
            phase=Phase(data["phase"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            input=StepInput.from_dict(data.get("input", {})),
            output=StepOutput.from_dict(data["output"]),
            metadata=StepMetadata.from_dict(data.get("metadata", {})),
        )


@dataclass
class UserDecision:
    """A user decision about generated content.

    Captures whether the user accepted, rejected, or edited AI output.
    """

    step_id: str
    phase: Phase
    decision: DecisionType
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    edits: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "type": "decision",
            "step_id": self.step_id,
            "phase": self.phase.value,
            "decision": self.decision.value,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.edits:
            result["edits"] = self.edits
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserDecision":
        """Create from dictionary."""
        return cls(
            step_id=data["step_id"],
            phase=Phase(data["phase"]),
            decision=DecisionType(data["decision"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            edits=data.get("edits"),
        )


@dataclass
class Artifact:
    """A generated artifact (brief, spec, flight plan).

    Captures the final output of a generation phase.
    """

    artifact_type: ArtifactType
    content: str
    file_path: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "type": "artifact",
            "artifact_type": self.artifact_type.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.file_path:
            result["file_path"] = self.file_path
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        """Create from dictionary."""
        return cls(
            artifact_type=ArtifactType(data["artifact_type"]),
            content=data["content"],
            file_path=data.get("file_path"),
            timestamp=datetime.fromisoformat(
                data.get("timestamp", datetime.now(UTC).isoformat())
            ),
        )


@dataclass
class GenerativeSpec:
    """Complete generative specification for a project.

    Contains all steps, decisions, and artifacts needed to
    reproduce or regenerate the software.
    """

    version: str
    waypoints_version: str
    source_project: str
    created_at: datetime
    model: str | None = None
    model_version: str | None = None
    initial_idea: str = ""
    steps: list[GenerativeStep] = field(default_factory=list)
    decisions: list[UserDecision] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)

    def to_header_dict(self) -> dict[str, Any]:
        """Convert header info to dictionary for serialization."""
        result: dict[str, Any] = {
            "_schema": "genspec",
            "_version": self.version,
            "waypoints_version": self.waypoints_version,
            "source_project": self.source_project,
            "created_at": self.created_at.isoformat(),
        }
        if self.model:
            result["model"] = self.model
        if self.model_version:
            result["model_version"] = self.model_version
        if self.initial_idea:
            result["initial_idea"] = self.initial_idea
        return result

    @classmethod
    def from_header_dict(cls, data: dict[str, Any]) -> "GenerativeSpec":
        """Create from header dictionary (without steps/decisions/artifacts)."""
        return cls(
            version=data.get("_version", "1.0"),
            waypoints_version=data.get("waypoints_version", "unknown"),
            source_project=data["source_project"],
            created_at=datetime.fromisoformat(data["created_at"]),
            model=data.get("model"),
            model_version=data.get("model_version"),
            initial_idea=data.get("initial_idea", ""),
        )

    def get_steps_by_phase(self, phase: Phase) -> list[GenerativeStep]:
        """Get all steps for a given phase."""
        return [s for s in self.steps if s.phase == phase]

    def get_artifact(self, artifact_type: ArtifactType) -> Artifact | None:
        """Get artifact by type."""
        for artifact in self.artifacts:
            if artifact.artifact_type == artifact_type:
                return artifact
        return None

    def summary(self) -> dict[str, Any]:
        """Get summary statistics about the spec."""
        phase_counts: dict[str, int] = {}
        for step in self.steps:
            phase_name = step.phase.value
            phase_counts[phase_name] = phase_counts.get(phase_name, 0) + 1

        total_cost = sum(s.metadata.cost_usd for s in self.steps if s.metadata.cost_usd)

        return {
            "source_project": self.source_project,
            "total_steps": len(self.steps),
            "total_decisions": len(self.decisions),
            "total_artifacts": len(self.artifacts),
            "phases": phase_counts,
            "total_cost_usd": total_cost,
            "model": self.model,
            "created_at": self.created_at.isoformat(),
        }
