"""Checklist receipt dataclass and validator.

The receipt pattern implements "trust but verify" - the model runs checklist
commands and code captures actual outputs as evidence. The receipt is then
verified by an LLM to ensure the evidence indicates success.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)


@dataclass
class WaypointContext:
    """Context about the waypoint this receipt is for."""

    title: str
    objective: str
    acceptance_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "title": self.title,
            "objective": self.objective,
            "acceptance_criteria": self.acceptance_criteria,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WaypointContext":
        """Create from dictionary."""
        return cls(
            title=data["title"],
            objective=data["objective"],
            acceptance_criteria=data.get("acceptance_criteria", []),
        )


@dataclass
class CriterionVerification:
    """Verification result for a single acceptance criterion."""

    index: int
    criterion: str
    status: Literal["verified", "failed"]
    evidence: str
    verified_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "index": self.index,
            "criterion": self.criterion,
            "status": self.status,
            "evidence": self.evidence,
            "verified_at": self.verified_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CriterionVerification":
        """Create from dictionary."""
        return cls(
            index=data["index"],
            criterion=data["criterion"],
            status=data["status"],
            evidence=data["evidence"],
            verified_at=datetime.fromisoformat(data["verified_at"]),
        )


@dataclass
class ChecklistItem:
    """A single checklist item with captured evidence."""

    item: str
    status: Literal["passed", "failed", "skipped"]
    command: str = ""  # The actual command that was run
    exit_code: int | None = None  # Command exit code
    stdout: str = ""  # Captured stdout
    stderr: str = ""  # Captured stderr
    captured_at: datetime | None = None  # When evidence was captured
    evidence: str = ""  # Legacy: model-written prose (for backwards compat)
    reason: str = ""  # Why it was skipped (for skipped items)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "item": self.item,
            "status": self.status,
        }
        if self.command:
            result["command"] = self.command
        if self.exit_code is not None:
            result["exit_code"] = self.exit_code
        if self.stdout:
            result["stdout"] = self.stdout
        if self.stderr:
            result["stderr"] = self.stderr
        if self.captured_at:
            result["captured_at"] = self.captured_at.isoformat()
        if self.evidence:
            result["evidence"] = self.evidence
        if self.reason:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChecklistItem":
        """Create from dictionary."""
        captured_at = None
        if data.get("captured_at"):
            captured_at = datetime.fromisoformat(data["captured_at"])
        return cls(
            item=data["item"],
            status=data["status"],
            command=data.get("command", ""),
            exit_code=data.get("exit_code"),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            captured_at=captured_at,
            evidence=data.get("evidence", ""),
            reason=data.get("reason", ""),
        )


@dataclass
class ChecklistReceipt:
    """Proof of work with captured evidence for a waypoint."""

    waypoint_id: str
    completed_at: datetime
    context: WaypointContext | None = None  # Waypoint context for traceability
    checklist: list[ChecklistItem] = field(default_factory=list)
    criteria_verification: list[CriterionVerification] = field(default_factory=list)

    def is_valid(self) -> bool:
        """Check if all non-skipped items passed."""
        return all(item.status in ("passed", "skipped") for item in self.checklist)

    def failed_items(self) -> list[ChecklistItem]:
        """Get list of failed checklist items."""
        return [item for item in self.checklist if item.status == "failed"]

    def has_captured_evidence(self) -> bool:
        """Check if receipt has real captured evidence (not just model prose)."""
        return any(item.exit_code is not None for item in self.checklist)

    def failed_criteria(self) -> list[CriterionVerification]:
        """Get list of failed acceptance criteria."""
        return [c for c in self.criteria_verification if c.status == "failed"]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "waypoint_id": self.waypoint_id,
            "completed_at": self.completed_at.isoformat(),
            "checklist": [item.to_dict() for item in self.checklist],
        }
        if self.context:
            result["context"] = self.context.to_dict()
        if self.criteria_verification:
            result["criteria_verification"] = [
                c.to_dict() for c in self.criteria_verification
            ]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChecklistReceipt":
        """Create from dictionary."""
        context = None
        if data.get("context"):
            context = WaypointContext.from_dict(data["context"])
        criteria_verification = [
            CriterionVerification.from_dict(c)
            for c in data.get("criteria_verification", [])
        ]
        return cls(
            waypoint_id=data["waypoint_id"],
            completed_at=datetime.fromisoformat(data["completed_at"]),
            context=context,
            checklist=[
                ChecklistItem.from_dict(item) for item in data.get("checklist", [])
            ],
            criteria_verification=criteria_verification,
        )

    def save(self, path: Path) -> None:
        """Save receipt to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        logger.info("Saved receipt to %s", path)

    @classmethod
    def load(cls, path: Path) -> "ChecklistReceipt":
        """Load receipt from JSON file."""
        data = json.loads(path.read_text())
        return cls.from_dict(data)


@dataclass
class ReceiptValidationResult:
    """Result of validating a receipt."""

    valid: bool
    message: str
    receipt: ChecklistReceipt | None = None


class ReceiptValidator:
    """Validates checklist receipts.

    The validator is the "dog" in the pilot-and-dog architecture -
    it bites if the pilot (model) didn't follow protocol.
    """

    def validate(self, receipt_path: Path) -> ReceiptValidationResult:
        """Validate that a receipt exists and is well-formed.

        Returns:
            ReceiptValidationResult with valid=True if receipt passes validation.
        """
        if not receipt_path.exists():
            logger.warning("Receipt not found: %s", receipt_path)
            return ReceiptValidationResult(
                valid=False,
                message="No receipt found - model did not produce checklist evidence",
            )

        try:
            receipt = ChecklistReceipt.load(receipt_path)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error("Invalid receipt format: %s - %s", receipt_path, e)
            return ReceiptValidationResult(
                valid=False,
                message=f"Receipt is malformed: {e}",
            )

        if not receipt.is_valid():
            failed = receipt.failed_items()
            failed_names = ", ".join(item.item for item in failed)
            logger.warning("Checklist failed: %s", failed_names)
            return ReceiptValidationResult(
                valid=False,
                message=f"Checklist failed: {failed_names}",
                receipt=receipt,
            )

        logger.info("Receipt validated: %s", receipt_path)
        return ReceiptValidationResult(
            valid=True,
            message="Receipt validated successfully",
            receipt=receipt,
        )

    def get_receipt_path(self, project: "Project", waypoint_id: str) -> Path:
        """Get the expected receipt path for a waypoint.

        Receipts are stored in:
        {project_dir}/receipts/{waypoint-id}-{timestamp}.json

        This returns the pattern to search for, not an exact path.
        """
        receipts_dir = project.get_path() / "receipts"
        # Normalize waypoint ID for filename (lowercase, no special chars)
        safe_id = waypoint_id.lower().replace("-", "")
        return receipts_dir / f"{safe_id}-*.json"

    def find_latest_receipt(self, project: "Project", waypoint_id: str) -> Path | None:
        """Find the most recent receipt for a waypoint."""
        receipts_dir = project.get_path() / "receipts"
        if not receipts_dir.exists():
            return None

        safe_id = waypoint_id.lower().replace("-", "")
        matching = sorted(
            receipts_dir.glob(f"{safe_id}-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return matching[0] if matching else None


@dataclass
class CapturedEvidence:
    """Evidence captured from running a command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    captured_at: datetime


class ReceiptBuilder:
    """Builds receipts from captured evidence during waypoint execution.

    Instead of relying on model-written prose, this captures actual command
    outputs and builds a verifiable receipt.
    """

    def __init__(
        self,
        waypoint_id: str,
        title: str,
        objective: str,
        acceptance_criteria: list[str] | None = None,
    ):
        self.waypoint_id = waypoint_id
        self.context = WaypointContext(
            title=title,
            objective=objective,
            acceptance_criteria=acceptance_criteria or [],
        )
        self.evidence: dict[str, CapturedEvidence] = {}
        self.criteria: dict[int, CriterionVerification] = {}

    def capture(self, category: str, evidence: CapturedEvidence) -> None:
        """Capture evidence for a checklist category.

        Args:
            category: The checklist category (lint, test, type, format)
            evidence: The captured command output
        """
        self.evidence[category] = evidence
        logger.debug(
            "Captured evidence for %s: exit_code=%d", category, evidence.exit_code
        )

    def capture_criterion(self, verification: CriterionVerification) -> None:
        """Capture verification for an acceptance criterion.

        Args:
            verification: The criterion verification result
        """
        self.criteria[verification.index] = verification
        logger.debug(
            "Captured criterion %d verification: %s",
            verification.index,
            verification.status,
        )

    def build(self) -> ChecklistReceipt:
        """Build receipt from captured evidence.

        Returns:
            ChecklistReceipt with waypoint context and captured evidence.
        """
        checklist_items = []
        for category, ev in self.evidence.items():
            checklist_items.append(
                ChecklistItem(
                    item=category,
                    status="passed" if ev.exit_code == 0 else "failed",
                    command=ev.command,
                    exit_code=ev.exit_code,
                    stdout=ev.stdout[:2000] if ev.stdout else "",  # Truncate
                    stderr=ev.stderr[:2000] if ev.stderr else "",  # Truncate
                    captured_at=ev.captured_at,
                )
            )

        # Sort criteria by index for consistent output
        criteria_list = sorted(self.criteria.values(), key=lambda c: c.index)

        return ChecklistReceipt(
            waypoint_id=self.waypoint_id,
            completed_at=datetime.now(),
            context=self.context,
            checklist=checklist_items,
            criteria_verification=criteria_list,
        )

    def has_evidence(self) -> bool:
        """Check if any evidence has been captured."""
        return len(self.evidence) > 0
