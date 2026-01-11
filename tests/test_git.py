"""Tests for the git module."""

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from waypoints.config.paths import reset_paths
from waypoints.git import (
    Checklist,
    ChecklistItem,
    ChecklistReceipt,
    GitConfig,
    GitService,
    ReceiptValidator,
)


@dataclass
class MockProject:
    """Mock project for testing."""

    path: Path

    def get_path(self) -> Path:
        return self.path


@pytest.fixture(autouse=True)
def reset_paths_singleton() -> None:
    """Reset the paths singleton before each test."""
    reset_paths()


class TestChecklistItem:
    """Tests for ChecklistItem dataclass."""

    def test_to_dict_passed(self) -> None:
        """Test serialization of a passed item."""
        item = ChecklistItem(
            item="Code passes linting",
            status="passed",
            evidence="Ran ruff check . - 0 errors",
        )
        result = item.to_dict()
        assert result == {
            "item": "Code passes linting",
            "status": "passed",
            "evidence": "Ran ruff check . - 0 errors",
        }

    def test_to_dict_skipped(self) -> None:
        """Test serialization of a skipped item."""
        item = ChecklistItem(
            item="Type checking",
            status="skipped",
            reason="No type checker configured",
        )
        result = item.to_dict()
        assert result == {
            "item": "Type checking",
            "status": "skipped",
            "reason": "No type checker configured",
        }

    def test_from_dict(self) -> None:
        """Test deserialization."""
        data = {
            "item": "Tests pass",
            "status": "failed",
            "evidence": "2 tests failed",
        }
        item = ChecklistItem.from_dict(data)
        assert item.item == "Tests pass"
        assert item.status == "failed"
        assert item.evidence == "2 tests failed"


class TestChecklistReceipt:
    """Tests for ChecklistReceipt dataclass."""

    def test_is_valid_all_passed(self) -> None:
        """Test validation with all items passed."""
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[
                ChecklistItem("Linting", "passed", "OK"),
                ChecklistItem("Tests", "passed", "10 passed"),
            ],
        )
        assert receipt.is_valid() is True

    def test_is_valid_with_skipped(self) -> None:
        """Test validation with some items skipped."""
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[
                ChecklistItem("Linting", "passed", "OK"),
                ChecklistItem("Type check", "skipped", reason="Not configured"),
            ],
        )
        assert receipt.is_valid() is True

    def test_is_valid_with_failed(self) -> None:
        """Test validation with a failed item."""
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[
                ChecklistItem("Linting", "passed", "OK"),
                ChecklistItem("Tests", "failed", "3 tests failed"),
            ],
        )
        assert receipt.is_valid() is False

    def test_failed_items(self) -> None:
        """Test getting failed items."""
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[
                ChecklistItem("Linting", "failed", "5 errors"),
                ChecklistItem("Tests", "passed", "OK"),
                ChecklistItem("Types", "failed", "10 errors"),
            ],
        )
        failed = receipt.failed_items()
        assert len(failed) == 2
        assert failed[0].item == "Linting"
        assert failed[1].item == "Types"

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Test saving and loading a receipt."""
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime(2026, 1, 8, 12, 0, 0),
            checklist=[
                ChecklistItem("Linting", "passed", "OK"),
            ],
        )
        file_path = tmp_path / "receipt.json"
        receipt.save(file_path)

        loaded = ChecklistReceipt.load(file_path)
        assert loaded.waypoint_id == "WP-001"
        assert loaded.completed_at == datetime(2026, 1, 8, 12, 0, 0)
        assert len(loaded.checklist) == 1
        assert loaded.checklist[0].item == "Linting"


class TestReceiptValidator:
    """Tests for ReceiptValidator."""

    def test_validate_missing_file(self, tmp_path: Path) -> None:
        """Test validation when receipt file doesn't exist."""
        validator = ReceiptValidator()
        result = validator.validate(tmp_path / "nonexistent.json")
        assert result.valid is False
        assert "No receipt found" in result.message

    def test_validate_malformed_json(self, tmp_path: Path) -> None:
        """Test validation with malformed JSON."""
        file_path = tmp_path / "bad.json"
        file_path.write_text("not valid json")

        validator = ReceiptValidator()
        result = validator.validate(file_path)
        assert result.valid is False
        assert "malformed" in result.message

    def test_validate_valid_receipt(self, tmp_path: Path) -> None:
        """Test validation of a valid receipt."""
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[ChecklistItem("Linting", "passed", "OK")],
        )
        file_path = tmp_path / "receipt.json"
        receipt.save(file_path)

        validator = ReceiptValidator()
        result = validator.validate(file_path)
        assert result.valid is True
        assert result.receipt is not None

    def test_validate_failed_checklist(self, tmp_path: Path) -> None:
        """Test validation of a receipt with failed items."""
        receipt = ChecklistReceipt(
            waypoint_id="WP-001",
            completed_at=datetime.now(),
            checklist=[ChecklistItem("Linting", "failed", "5 errors")],
        )
        file_path = tmp_path / "receipt.json"
        receipt.save(file_path)

        validator = ReceiptValidator()
        result = validator.validate(file_path)
        assert result.valid is False
        assert "Checklist failed" in result.message

    def test_find_latest_receipt(self, tmp_path: Path) -> None:
        """Test finding the latest receipt for a waypoint."""
        # Create receipts directory
        receipts_dir = tmp_path / "receipts"
        receipts_dir.mkdir()

        # Create two receipts with different timestamps
        (receipts_dir / "wp001-20260108-100000.json").write_text(
            json.dumps(
                {
                    "waypoint_id": "WP-001",
                    "completed_at": "2026-01-08T10:00:00",
                    "checklist": [],
                }
            )
        )
        (receipts_dir / "wp001-20260108-120000.json").write_text(
            json.dumps(
                {
                    "waypoint_id": "WP-001",
                    "completed_at": "2026-01-08T12:00:00",
                    "checklist": [],
                }
            )
        )

        validator = ReceiptValidator()
        mock_project = MockProject(tmp_path)
        latest = validator.find_latest_receipt(mock_project, "WP-001")
        assert latest is not None
        assert "120000" in latest.name


class TestGitService:
    """Tests for GitService."""

    def test_is_git_repo_true(self, tmp_path: Path) -> None:
        """Test detecting a git repository."""
        # Initialize a git repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)

        service = GitService(tmp_path)
        assert service.is_git_repo() is True

    def test_is_git_repo_false(self, tmp_path: Path) -> None:
        """Test detecting a non-git directory."""
        service = GitService(tmp_path)
        assert service.is_git_repo() is False

    def test_init_repo(self, tmp_path: Path) -> None:
        """Test initializing a git repository."""
        service = GitService(tmp_path)
        result = service.init_repo()

        assert result.success is True
        assert (tmp_path / ".git").exists()
        assert (tmp_path / ".gitignore").exists()

    def test_init_repo_already_exists(self, tmp_path: Path) -> None:
        """Test init when repo already exists."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)

        service = GitService(tmp_path)
        result = service.init_repo()

        assert result.success is True
        assert "Already" in result.message

    def test_stage_and_commit(self, tmp_path: Path) -> None:
        """Test staging and committing files."""
        # Initialize repo
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True,
        )

        # Create a file
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        service = GitService(tmp_path)
        service.stage_files("test.txt")
        result = service.commit("test commit")

        assert result.success is True

    def test_commit_nothing_to_commit(self, tmp_path: Path) -> None:
        """Test commit when there's nothing staged."""
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)

        service = GitService(tmp_path)
        result = service.commit("empty commit")

        assert result.success is True
        assert "Nothing to commit" in result.message

    def test_tag(self, tmp_path: Path) -> None:
        """Test creating a tag."""
        # Initialize repo with initial commit
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path,
            capture_output=True,
        )
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=tmp_path,
            capture_output=True,
        )

        service = GitService(tmp_path)
        result = service.tag("v1.0.0", "First release")

        assert result.success is True


class TestGitConfig:
    """Tests for GitConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = GitConfig()
        assert config.auto_commit is True
        assert config.auto_init is True
        assert config.run_checklist is True
        assert config.create_phase_tags is True
        assert config.create_waypoint_tags is False

    def test_load_from_file(self, tmp_path: Path) -> None:
        """Test loading config from a file."""
        from waypoints.config.paths import get_paths

        # Initialize paths singleton with tmp_path as workspace
        get_paths(tmp_path)

        config_path = tmp_path / ".waypoints" / "git-config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps(
                {
                    "auto_commit": False,
                    "create_waypoint_tags": True,
                }
            )
        )

        config = GitConfig.load()

        assert config.auto_commit is False
        assert config.create_waypoint_tags is True


class TestChecklist:
    """Tests for Checklist."""

    def test_default_checklist(self) -> None:
        """Test default checklist items."""
        checklist = Checklist()
        assert len(checklist.items) == 4
        assert "Code passes linting" in checklist.items

    def test_load_creates_default(self, tmp_path: Path) -> None:
        """Test that load creates default checklist if none exists."""
        mock_project = MockProject(tmp_path)
        checklist = Checklist.load(mock_project)
        assert len(checklist.items) == 4
        assert (tmp_path / "checklist.yaml").exists()

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Test saving and loading a custom checklist."""
        mock_project = MockProject(tmp_path)
        checklist = Checklist(items=["Custom check 1", "Custom check 2"])
        checklist.save(mock_project)

        loaded = Checklist.load(mock_project)
        assert loaded.items == ["Custom check 1", "Custom check 2"]

    def test_to_prompt(self) -> None:
        """Test formatting checklist for prompts."""
        checklist = Checklist(items=["Check 1", "Check 2"])
        prompt = checklist.to_prompt()

        assert "Check 1" in prompt
        assert "Check 2" in prompt
        assert "checklist receipt" in prompt.lower()
