"""Data models for the verification system."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ComparisonVerdict(Enum):
    """Result of comparing two artifacts."""

    EQUIVALENT = "equivalent"  # Semantically equivalent
    DIFFERENT = "different"  # Meaningfully different
    UNCERTAIN = "uncertain"  # Cannot determine with confidence


class VerificationStatus(Enum):
    """Overall status of a verification run."""

    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"  # Some steps passed, some failed
    ERROR = "error"  # Pipeline error, not comparison failure


@dataclass
class ComparisonResult:
    """Result of comparing two artifacts (specs, plans, etc.)."""

    verdict: ComparisonVerdict
    confidence: float  # 0.0 to 1.0
    rationale: str  # Human-readable explanation
    differences: list[str] = field(default_factory=list)  # Specific differences found
    artifact_type: str = ""  # "spec", "plan", "waypoint"
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "differences": self.differences,
            "artifact_type": self.artifact_type,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ComparisonResult":
        """Create from dictionary."""
        return cls(
            verdict=ComparisonVerdict(data["verdict"]),
            confidence=data["confidence"],
            rationale=data["rationale"],
            differences=data.get("differences", []),
            artifact_type=data.get("artifact_type", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


@dataclass
class VerificationStep:
    """Result of a single step in the verification pipeline."""

    name: str  # "spec_comparison", "plan_comparison", "test_generation"
    status: str  # "pass", "fail", "skipped", "error"
    result: ComparisonResult | None = None  # For comparison steps
    message: str = ""  # Additional info or error message
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "status": self.status,
            "result": self.result.to_dict() if self.result else None,
            "message": self.message,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class VerificationReport:
    """Full verification report for a genspec."""

    genspec_path: str
    reference_path: str
    overall_status: VerificationStatus
    steps: list[VerificationStep] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "genspec_path": self.genspec_path,
            "reference_path": self.reference_path,
            "overall_status": self.overall_status.value,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    def add_step(self, step: VerificationStep) -> None:
        """Add a step to the report."""
        self.steps.append(step)

    def finalize(self) -> None:
        """Finalize the report with completion time and overall status."""
        self.completed_at = datetime.now()

        # Determine overall status from steps
        statuses = [s.status for s in self.steps]
        if all(s == "pass" for s in statuses):
            self.overall_status = VerificationStatus.PASS
        elif all(s == "fail" for s in statuses):
            self.overall_status = VerificationStatus.FAIL
        elif "error" in statuses:
            self.overall_status = VerificationStatus.ERROR
        else:
            self.overall_status = VerificationStatus.PARTIAL
