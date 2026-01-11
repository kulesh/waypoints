"""Checklist receipt dataclass and validator.

The receipt pattern implements "trust but verify" - the model runs conceptual
checklist items and produces a receipt as proof of work. Code validates the
receipt exists and is well-formed before allowing commits.
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
class ChecklistItem:
    """A single checklist item result."""

    item: str
    status: Literal["passed", "failed", "skipped"]
    evidence: str = ""  # How the check was verified (for passed/failed)
    reason: str = ""  # Why it was skipped (for skipped)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result: dict[str, Any] = {
            "item": self.item,
            "status": self.status,
        }
        if self.evidence:
            result["evidence"] = self.evidence
        if self.reason:
            result["reason"] = self.reason
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChecklistItem":
        """Create from dictionary."""
        return cls(
            item=data["item"],
            status=data["status"],
            evidence=data.get("evidence", ""),
            reason=data.get("reason", ""),
        )


@dataclass
class ChecklistReceipt:
    """Proof that the model ran the checklist for a waypoint."""

    waypoint_id: str
    completed_at: datetime
    checklist: list[ChecklistItem] = field(default_factory=list)

    def is_valid(self) -> bool:
        """Check if all non-skipped items passed."""
        return all(item.status in ("passed", "skipped") for item in self.checklist)

    def failed_items(self) -> list[ChecklistItem]:
        """Get list of failed checklist items."""
        return [item for item in self.checklist if item.status == "failed"]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "waypoint_id": self.waypoint_id,
            "completed_at": self.completed_at.isoformat(),
            "checklist": [item.to_dict() for item in self.checklist],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChecklistReceipt":
        """Create from dictionary."""
        return cls(
            waypoint_id=data["waypoint_id"],
            completed_at=datetime.fromisoformat(data["completed_at"]),
            checklist=[
                ChecklistItem.from_dict(item) for item in data.get("checklist", [])
            ],
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
