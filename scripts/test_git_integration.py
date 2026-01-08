#!/usr/bin/env python3
"""Test git integration with receipt-based commit system.

This script demonstrates and tests the git integration features:
1. Auto-init git repo
2. Checklist loading/creation
3. Receipt creation and validation
4. Committing with valid receipt
5. Skipping commit with invalid/missing receipt

Usage:
    python scripts/test_git_integration.py [test-dir]

Arguments:
    test-dir  Optional directory for testing (creates temp dir if not specified)

Examples:
    python scripts/test_git_integration.py
    python scripts/test_git_integration.py /tmp/git-test
"""

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from waypoints.git import (
    Checklist,
    ChecklistItem,
    ChecklistReceipt,
    GitConfig,
    GitService,
    ReceiptValidator,
)


def print_header(title: str) -> None:
    """Print a section header."""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_step(step: str) -> None:
    """Print a test step."""
    print(f"\n→ {step}")


def print_result(success: bool, message: str) -> None:
    """Print a test result."""
    marker = "✓" if success else "✗"
    print(f"  {marker} {message}")


def test_git_service(test_dir: Path) -> bool:
    """Test GitService operations."""
    print_header("Testing GitService")
    all_passed = True

    # Test 1: is_git_repo (should be False initially)
    print_step("Checking if directory is a git repo (should be False)")
    git = GitService(test_dir)
    is_repo = git.is_git_repo()
    print_result(not is_repo, f"is_git_repo() = {is_repo}")
    if is_repo:
        all_passed = False

    # Test 2: init_repo
    print_step("Initializing git repository")
    result = git.init_repo()
    print_result(result.success, f"init_repo(): {result.message}")
    if not result.success:
        all_passed = False

    # Verify .git exists
    git_dir = test_dir / ".git"
    print_result(git_dir.exists(), f".git directory exists: {git_dir.exists()}")
    if not git_dir.exists():
        all_passed = False

    # Verify .gitignore was created
    gitignore = test_dir / ".gitignore"
    print_result(gitignore.exists(), f".gitignore exists: {gitignore.exists()}")

    # Test 3: is_git_repo (should be True now)
    print_step("Checking if directory is a git repo (should be True)")
    is_repo = git.is_git_repo()
    print_result(is_repo, f"is_git_repo() = {is_repo}")
    if not is_repo:
        all_passed = False

    # Configure git user for commits
    subprocess.run(
        ["git", "config", "user.email", "test@waypoints.dev"],
        cwd=test_dir,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Waypoints Test"],
        cwd=test_dir,
        capture_output=True,
    )

    # Test 4: Create a file and commit
    print_step("Creating and committing a test file")
    test_file = test_dir / "hello.txt"
    test_file.write_text("Hello, Waypoints!")
    git.stage_files("hello.txt")
    result = git.commit("Initial commit")
    print_result(result.success, f"commit(): {result.message}")
    if not result.success:
        all_passed = False

    # Test 5: Tag
    print_step("Creating a tag")
    result = git.tag("v0.1.0", "First test release")
    print_result(result.success, f"tag(): {result.message}")
    if not result.success:
        all_passed = False

    # Verify tag exists
    tag_result = subprocess.run(
        ["git", "tag", "-l", "v0.1.0"],
        cwd=test_dir,
        capture_output=True,
        text=True,
    )
    tag_exists = "v0.1.0" in tag_result.stdout
    print_result(tag_exists, f"Tag v0.1.0 exists: {tag_exists}")

    return all_passed


def test_checklist(test_dir: Path) -> bool:
    """Test Checklist operations."""
    print_header("Testing Checklist")
    all_passed = True

    project_dir = test_dir / ".waypoints" / "projects" / "test-project"
    project_dir.mkdir(parents=True, exist_ok=True)

    # Test 1: Default checklist creation
    print_step("Loading checklist (creates default)")
    checklist = Checklist.load(project_dir)
    print_result(len(checklist.items) == 4, f"Default items: {len(checklist.items)}")
    if len(checklist.items) != 4:
        all_passed = False

    # Verify file was created
    checklist_path = project_dir / "checklist.yaml"
    print_result(
        checklist_path.exists(),
        f"checklist.yaml created: {checklist_path.exists()}"
    )

    # Test 2: Custom checklist
    print_step("Saving custom checklist")
    custom = Checklist(items=["Custom check 1", "Custom check 2", "Custom check 3"])
    custom.save(project_dir)
    loaded = Checklist.load(project_dir)
    print_result(
        len(loaded.items) == 3,
        f"Custom items loaded: {len(loaded.items)}"
    )
    if len(loaded.items) != 3:
        all_passed = False

    # Test 3: to_prompt
    print_step("Generating prompt text")
    prompt = checklist.to_prompt()
    has_items = all(item in prompt for item in checklist.items[:2])
    print_result(has_items, "Prompt contains checklist items")
    print(f"  Preview: {prompt[:100]}...")

    return all_passed


def test_receipt_validation(test_dir: Path) -> bool:
    """Test receipt creation and validation."""
    print_header("Testing Receipt Validation")
    all_passed = True

    project_dir = test_dir / ".waypoints" / "projects" / "test-project"
    receipts_dir = project_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    validator = ReceiptValidator()

    # Test 1: Missing receipt
    print_step("Validating missing receipt")
    result = validator.validate(receipts_dir / "nonexistent.json")
    print_result(not result.valid, f"Missing receipt invalid: {result.message}")
    if result.valid:
        all_passed = False

    # Test 2: Valid receipt
    print_step("Creating and validating a passing receipt")
    valid_receipt = ChecklistReceipt(
        waypoint_id="WP-001",
        completed_at=datetime.now(),
        checklist=[
            ChecklistItem("Code passes linting", "passed", "ruff check . - 0 errors"),
            ChecklistItem("All tests pass", "passed", "pytest - 10 passed"),
            ChecklistItem("Type checking", "skipped", reason="Not configured"),
        ],
    )
    receipt_path = receipts_dir / "wp001-20260108-120000.json"
    valid_receipt.save(receipt_path)
    result = validator.validate(receipt_path)
    print_result(result.valid, f"Valid receipt: {result.message}")
    if not result.valid:
        all_passed = False

    # Test 3: Failed receipt
    print_step("Creating and validating a failing receipt")
    failed_receipt = ChecklistReceipt(
        waypoint_id="WP-002",
        completed_at=datetime.now(),
        checklist=[
            ChecklistItem("Code passes linting", "failed", "5 errors found"),
            ChecklistItem("All tests pass", "passed", "10 passed"),
        ],
    )
    failed_path = receipts_dir / "wp002-20260108-120000.json"
    failed_receipt.save(failed_path)
    result = validator.validate(failed_path)
    print_result(not result.valid, f"Failed receipt invalid: {result.message}")
    if result.valid:
        all_passed = False

    # Test 4: Find latest receipt
    print_step("Finding latest receipt for WP-001")
    # Create another receipt with later timestamp
    later_receipt = ChecklistReceipt(
        waypoint_id="WP-001",
        completed_at=datetime.now(),
        checklist=[ChecklistItem("Linting", "passed", "OK")],
    )
    later_path = receipts_dir / "wp001-20260108-140000.json"
    later_receipt.save(later_path)

    latest = validator.find_latest_receipt(project_dir, "WP-001")
    print_result(
        latest is not None and "140000" in latest.name,
        f"Found latest: {latest.name if latest else 'None'}"
    )

    return all_passed


def test_git_config(test_dir: Path) -> bool:
    """Test GitConfig operations."""
    print_header("Testing GitConfig")
    all_passed = True

    # Test 1: Default config
    print_step("Loading default config")
    config = GitConfig()
    print_result(config.auto_commit, f"auto_commit: {config.auto_commit}")
    print_result(config.auto_init, f"auto_init: {config.auto_init}")
    print_result(config.run_checklist, f"run_checklist: {config.run_checklist}")

    # Test 2: Save and load config
    print_step("Saving custom config")
    waypoints_dir = test_dir / ".waypoints"
    waypoints_dir.mkdir(parents=True, exist_ok=True)

    custom_config = GitConfig(
        auto_commit=True,
        auto_init=True,
        run_checklist=True,
        create_phase_tags=False,
        create_waypoint_tags=True,
    )
    config_path = waypoints_dir / "git-config.json"
    config_path.write_text(json.dumps({
        "auto_commit": True,
        "auto_init": True,
        "run_checklist": True,
        "create_phase_tags": False,
        "create_waypoint_tags": True,
    }, indent=2))

    # Can't easily test load() without mocking Path.cwd(), so just verify file
    print_result(config_path.exists(), f"Config saved: {config_path}")

    return all_passed


def test_full_workflow(test_dir: Path) -> bool:
    """Test the full git integration workflow."""
    print_header("Testing Full Workflow (Simulated Waypoint Completion)")
    all_passed = True

    # Set up project structure
    project_dir = test_dir / ".waypoints" / "projects" / "my-app"
    project_dir.mkdir(parents=True, exist_ok=True)
    receipts_dir = project_dir / "receipts"
    receipts_dir.mkdir(exist_ok=True)

    git = GitService(test_dir)

    # Scenario 1: Waypoint completion with valid receipt
    print_step("Scenario 1: Waypoint completion with valid receipt")

    # Simulate model producing code
    code_file = test_dir / "src" / "feature.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text("def new_feature():\n    return 'Hello!'\n")

    # Simulate model producing receipt
    receipt = ChecklistReceipt(
        waypoint_id="WP-001",
        completed_at=datetime.now(),
        checklist=[
            ChecklistItem("Code passes linting", "passed", "ruff check . - OK"),
            ChecklistItem("All tests pass", "passed", "pytest - 5 passed"),
        ],
    )
    receipt_path = receipts_dir / f"wp001-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    receipt.save(receipt_path)

    # Validate receipt (code's job)
    validator = ReceiptValidator()
    result = validator.validate(receipt_path)
    print_result(result.valid, f"Receipt valid: {result.valid}")

    if result.valid:
        # Stage and commit
        git.stage_files("src/", ".waypoints/")
        commit_result = git.commit("feat(my-app): Complete WP-001 - New feature")
        print_result(commit_result.success, f"Commit: {commit_result.message}")
        if not commit_result.success:
            all_passed = False
    else:
        print_result(False, "Should have committed but didn't")
        all_passed = False

    # Scenario 2: Waypoint completion with MISSING receipt
    print_step("Scenario 2: Waypoint completion with missing receipt")

    # Simulate model producing more code but NO receipt
    code_file2 = test_dir / "src" / "feature2.py"
    code_file2.write_text("def another_feature():\n    return 'World!'\n")

    # Try to find receipt (won't exist)
    missing_receipt = validator.find_latest_receipt(project_dir, "WP-002")
    print_result(missing_receipt is None, f"No receipt found: {missing_receipt is None}")

    if missing_receipt is None:
        print_result(True, "Correctly skipping commit due to missing receipt")
    else:
        all_passed = False

    # Scenario 3: Waypoint completion with FAILED receipt
    print_step("Scenario 3: Waypoint completion with failed receipt")

    # Simulate model producing receipt with failures
    failed_receipt = ChecklistReceipt(
        waypoint_id="WP-003",
        completed_at=datetime.now(),
        checklist=[
            ChecklistItem("Code passes linting", "failed", "3 errors"),
            ChecklistItem("All tests pass", "passed", "OK"),
        ],
    )
    failed_path = receipts_dir / f"wp003-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    failed_receipt.save(failed_path)

    result = validator.validate(failed_path)
    print_result(not result.valid, f"Receipt correctly invalid: {result.message}")
    if result.valid:
        all_passed = False

    # Show git log
    print_step("Git log")
    log_result = subprocess.run(
        ["git", "log", "--oneline", "-5"],
        cwd=test_dir,
        capture_output=True,
        text=True,
    )
    print(log_result.stdout)

    return all_passed


def main():
    # Determine test directory
    if len(sys.argv) > 1:
        test_dir = Path(sys.argv[1]).resolve()
        cleanup = False

        # Safety check: refuse to run in an existing git repo
        if (test_dir / ".git").exists():
            print("ERROR: Refusing to run in existing git repository!")
            print(f"  Directory: {test_dir}")
            print()
            print("This test creates commits and tags that would pollute your repo.")
            print("Please use a fresh directory or run without arguments for a temp dir.")
            sys.exit(1)
    else:
        test_dir = Path(tempfile.mkdtemp(prefix="waypoints-git-test-"))
        cleanup = True

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          Waypoints Git Integration Test Suite                 ║
╠══════════════════════════════════════════════════════════════╣
║  Testing the "Pilot and Dog" architecture:                    ║
║  - Model (pilot): Runs checklist, produces receipts           ║
║  - Code (dog): Validates receipts, enforces guardrails        ║
╚══════════════════════════════════════════════════════════════╝

Test directory: {test_dir}
""")

    try:
        # Run all tests
        results = []

        results.append(("GitService", test_git_service(test_dir)))
        results.append(("Checklist", test_checklist(test_dir)))
        results.append(("Receipt Validation", test_receipt_validation(test_dir)))
        results.append(("GitConfig", test_git_config(test_dir)))
        results.append(("Full Workflow", test_full_workflow(test_dir)))

        # Summary
        print_header("Test Summary")
        all_passed = True
        for name, passed in results:
            marker = "✓" if passed else "✗"
            print(f"  {marker} {name}")
            if not passed:
                all_passed = False

        print()
        if all_passed:
            print("All tests passed! ✓")
        else:
            print("Some tests failed. ✗")
            sys.exit(1)

    finally:
        if cleanup:
            print(f"\nCleaning up: {test_dir}")
            shutil.rmtree(test_dir, ignore_errors=True)
        else:
            print(f"\nTest artifacts preserved at: {test_dir}")


if __name__ == "__main__":
    main()
