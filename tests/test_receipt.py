"""Tests for enhanced receipt system with captured evidence."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from waypoints.git.receipt import (
    CapturedEvidence,
    ChecklistItem,
    ChecklistReceipt,
    ReceiptBuilder,
    WaypointContext,
)


class TestWaypointContext:
    """Tests for WaypointContext dataclass."""

    def test_create_context(self) -> None:
        """Test creating a waypoint context."""
        ctx = WaypointContext(
            title="Implement login",
            objective="Create user authentication",
            acceptance_criteria=["Users can login", "Passwords are hashed"],
        )
        assert ctx.title == "Implement login"
        assert ctx.objective == "Create user authentication"
        assert len(ctx.acceptance_criteria) == 2

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        ctx = WaypointContext(
            title="Test",
            objective="Testing",
            acceptance_criteria=["Criterion 1"],
        )
        d = ctx.to_dict()
        assert d["title"] == "Test"
        assert d["objective"] == "Testing"
        assert d["acceptance_criteria"] == ["Criterion 1"]

    def test_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        d = {
            "title": "Test",
            "objective": "Testing",
            "acceptance_criteria": ["Criterion 1", "Criterion 2"],
        }
        ctx = WaypointContext.from_dict(d)
        assert ctx.title == "Test"
        assert ctx.objective == "Testing"
        assert len(ctx.acceptance_criteria) == 2


class TestEnhancedChecklistItem:
    """Tests for enhanced ChecklistItem with captured evidence."""

    def test_create_with_captured_evidence(self) -> None:
        """Test creating an item with captured command output."""
        item = ChecklistItem(
            item="linting",
            status="passed",
            command="ruff check .",
            exit_code=0,
            stdout="All checks passed!",
            stderr="",
            captured_at=datetime.now(),
        )
        assert item.item == "linting"
        assert item.status == "passed"
        assert item.command == "ruff check ."
        assert item.exit_code == 0
        assert item.stdout == "All checks passed!"

    def test_to_dict_with_evidence(self) -> None:
        """Test serialization includes captured evidence."""
        now = datetime.now()
        item = ChecklistItem(
            item="tests",
            status="passed",
            command="pytest -v",
            exit_code=0,
            stdout="10 passed",
            stderr="",
            captured_at=now,
        )
        d = item.to_dict()
        assert d["item"] == "tests"
        assert d["command"] == "pytest -v"
        assert d["exit_code"] == 0
        assert d["stdout"] == "10 passed"
        assert d["captured_at"] == now.isoformat()

    def test_from_dict_with_evidence(self) -> None:
        """Test deserialization restores captured evidence."""
        now = datetime.now()
        d = {
            "item": "tests",
            "status": "passed",
            "command": "pytest -v",
            "exit_code": 0,
            "stdout": "10 passed",
            "stderr": "",
            "captured_at": now.isoformat(),
        }
        item = ChecklistItem.from_dict(d)
        assert item.command == "pytest -v"
        assert item.exit_code == 0
        assert item.stdout == "10 passed"
        assert item.captured_at == now

    def test_backwards_compat_legacy_evidence(self) -> None:
        """Test legacy evidence field still works."""
        d = {
            "item": "linting",
            "status": "passed",
            "evidence": "Ran ruff - 0 errors",
        }
        item = ChecklistItem.from_dict(d)
        assert item.evidence == "Ran ruff - 0 errors"
        assert item.exit_code is None  # No captured evidence


class TestEnhancedChecklistReceipt:
    """Tests for enhanced ChecklistReceipt with context."""

    def test_create_with_context(self) -> None:
        """Test creating receipt with waypoint context."""
        ctx = WaypointContext(
            title="Test waypoint",
            objective="Testing",
            acceptance_criteria=["Tests pass"],
        )
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            context=ctx,
            checklist=[],
        )
        assert receipt.context is not None
        assert receipt.context.title == "Test waypoint"

    def test_has_captured_evidence_true(self) -> None:
        """Test detecting receipt has captured evidence."""
        item = ChecklistItem(
            item="linting",
            status="passed",
            exit_code=0,
        )
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[item],
        )
        assert receipt.has_captured_evidence() is True

    def test_has_captured_evidence_false(self) -> None:
        """Test detecting receipt has only legacy evidence."""
        item = ChecklistItem(
            item="linting",
            status="passed",
            evidence="Ran ruff - 0 errors",  # Legacy format
        )
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[item],
        )
        assert receipt.has_captured_evidence() is False

    def test_to_dict_includes_context(self) -> None:
        """Test serialization includes context."""
        ctx = WaypointContext(
            title="Test",
            objective="Testing",
            acceptance_criteria=["Criterion"],
        )
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            context=ctx,
            checklist=[],
        )
        d = receipt.to_dict()
        assert "context" in d
        assert d["context"]["title"] == "Test"

    def test_from_dict_restores_context(self) -> None:
        """Test deserialization restores context."""
        d = {
            "waypoint_id": "WP-001",
            "completed_at": datetime.now().isoformat(),
            "context": {
                "title": "Test",
                "objective": "Testing",
                "acceptance_criteria": ["Criterion"],
            },
            "checklist": [],
        }
        receipt = ChecklistReceipt.from_dict(d)
        assert receipt.context is not None
        assert receipt.context.title == "Test"

    def test_backwards_compat_no_context(self) -> None:
        """Test loading legacy receipt without context."""
        d = {
            "waypoint_id": "WP-001",
            "completed_at": datetime.now().isoformat(),
            "checklist": [],
        }
        receipt = ChecklistReceipt.from_dict(d)
        assert receipt.context is None


class TestCapturedEvidence:
    """Tests for CapturedEvidence dataclass."""

    def test_create_evidence(self) -> None:
        """Test creating captured evidence."""
        evidence = CapturedEvidence(
            command="pytest -v",
            exit_code=0,
            stdout="10 passed in 1.5s",
            stderr="",
            captured_at=datetime.now(),
        )
        assert evidence.command == "pytest -v"
        assert evidence.exit_code == 0
        assert "10 passed" in evidence.stdout

    def test_failed_command(self) -> None:
        """Test capturing failed command output."""
        evidence = CapturedEvidence(
            command="ruff check .",
            exit_code=1,
            stdout="",
            stderr="Found 5 errors",
            captured_at=datetime.now(),
        )
        assert evidence.exit_code == 1
        assert "errors" in evidence.stderr


class TestReceiptBuilder:
    """Tests for ReceiptBuilder class."""

    def test_create_builder(self) -> None:
        """Test creating a receipt builder."""
        builder = ReceiptBuilder(
            waypoint_id="WP-001",
            title="Test waypoint",
            objective="Testing the builder",
            acceptance_criteria=["Tests pass"],
        )
        assert builder.waypoint_id == "WP-001"
        assert builder.context.title == "Test waypoint"

    def test_capture_evidence(self) -> None:
        """Test capturing evidence."""
        builder = ReceiptBuilder(
            waypoint_id="WP-001",
            title="Test",
            objective="Testing",
        )
        evidence = CapturedEvidence(
            command="pytest -v",
            exit_code=0,
            stdout="10 passed",
            stderr="",
            captured_at=datetime.now(),
        )
        builder.capture("tests", evidence)
        assert builder.has_evidence()
        assert "tests" in builder.evidence

    def test_build_receipt(self) -> None:
        """Test building receipt from captured evidence."""
        builder = ReceiptBuilder(
            waypoint_id="WP-001",
            title="Test",
            objective="Testing",
            acceptance_criteria=["Criterion"],
        )
        builder.capture(
            "linting",
            CapturedEvidence(
                command="ruff check .",
                exit_code=0,
                stdout="All passed",
                stderr="",
                captured_at=datetime.now(),
            ),
        )
        builder.capture(
            "tests",
            CapturedEvidence(
                command="pytest",
                exit_code=0,
                stdout="5 passed",
                stderr="",
                captured_at=datetime.now(),
            ),
        )

        receipt = builder.build()
        assert receipt.waypoint_id == "WP-001"
        assert receipt.context is not None
        assert receipt.context.title == "Test"
        assert len(receipt.checklist) == 2
        assert receipt.is_valid()
        assert receipt.has_captured_evidence()

    def test_build_with_failures(self) -> None:
        """Test building receipt with failed commands."""
        builder = ReceiptBuilder(
            waypoint_id="WP-001",
            title="Test",
            objective="Testing",
        )
        builder.capture(
            "linting",
            CapturedEvidence(
                command="ruff check .",
                exit_code=1,  # Failed
                stdout="",
                stderr="5 errors found",
                captured_at=datetime.now(),
            ),
        )

        receipt = builder.build()
        assert not receipt.is_valid()
        assert len(receipt.failed_items()) == 1

    def test_truncates_long_output(self) -> None:
        """Test that long outputs are truncated."""
        builder = ReceiptBuilder(
            waypoint_id="WP-001",
            title="Test",
            objective="Testing",
        )
        long_output = "x" * 5000  # Longer than 2000 char limit
        builder.capture(
            "tests",
            CapturedEvidence(
                command="pytest",
                exit_code=0,
                stdout=long_output,
                stderr="",
                captured_at=datetime.now(),
            ),
        )

        receipt = builder.build()
        assert len(receipt.checklist[0].stdout) <= 2000

    def test_has_evidence_empty(self) -> None:
        """Test has_evidence returns false when empty."""
        builder = ReceiptBuilder(
            waypoint_id="WP-001",
            title="Test",
            objective="Testing",
        )
        assert not builder.has_evidence()


class TestReceiptPersistence:
    """Tests for saving and loading receipts."""

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Test saving and loading receipt with full evidence."""
        ctx = WaypointContext(
            title="Test",
            objective="Testing",
            acceptance_criteria=["Criterion"],
        )
        item = ChecklistItem(
            item="linting",
            status="passed",
            command="ruff check .",
            exit_code=0,
            stdout="All passed",
            stderr="",
            captured_at=datetime.now(),
        )
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            context=ctx,
            checklist=[item],
        )

        path = tmp_path / "receipt.json"
        receipt.save(path)

        loaded = ChecklistReceipt.load(path)
        assert loaded.waypoint_id == "WP-001"
        assert loaded.context is not None
        assert loaded.context.title == "Test"
        assert len(loaded.checklist) == 1
        assert loaded.checklist[0].command == "ruff check ."
        assert loaded.checklist[0].exit_code == 0
