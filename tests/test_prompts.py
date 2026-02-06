"""Tests for LLM prompts."""

from datetime import datetime
from pathlib import Path

from waypoints.git.config import Checklist
from waypoints.git.receipt import ChecklistItem, ChecklistReceipt
from waypoints.llm.prompts.fly import build_execution_prompt, build_verification_prompt
from waypoints.models.waypoint import Waypoint


def test_verification_prompt_includes_soft_evidence() -> None:
    """Soft validation evidence should appear when present."""
    receipt = ChecklistReceipt(
        waypoint_id="WP-001",
        completed_at=datetime.now(),
        checklist=[
            ChecklistItem(
                item="tests",
                status="passed",
                command="pytest -v",
                exit_code=0,
                stdout="10 passed",
            )
        ],
        soft_checklist=[
            ChecklistItem(
                item="linting",
                status="failed",
                command="ruff check .",
                exit_code=1,
                stderr="Found errors",
            )
        ],
    )

    prompt = build_verification_prompt(receipt)
    assert "Soft Validation Evidence" in prompt
    assert "ruff check ." in prompt
    assert "Found errors" in prompt
    assert "host evidence as authoritative" in prompt


def test_verification_prompt_omits_soft_section_when_empty() -> None:
    """Soft validation section should be omitted when empty."""
    receipt = ChecklistReceipt(
        waypoint_id="WP-002",
        completed_at=datetime.now(),
        checklist=[
            ChecklistItem(
                item="tests",
                status="passed",
                command="pytest -v",
                exit_code=0,
                stdout="10 passed",
            )
        ],
    )

    prompt = build_verification_prompt(receipt)
    assert "Soft Validation Evidence" not in prompt


def test_execution_prompt_includes_resolution_notes() -> None:
    """Resolution notes should be injected into the execution prompt."""
    waypoint = Waypoint(
        id="WP-001",
        title="Debug Menu Preview",
        objective="Ensure the live preview updates correctly.",
        acceptance_criteria=["Preview updates on change"],
        resolution_notes=["Live preview does not refresh on state change."],
    )
    prompt = build_execution_prompt(
        waypoint,
        "Spec content",
        Path("project"),
        Checklist(items=["Run tests"]),
    )

    assert "Resolution Notes" in prompt
    assert "Live preview does not refresh" in prompt


def test_execution_prompt_includes_directory_policy_context() -> None:
    """Prompt should include project memory directory policy when provided."""
    waypoint = Waypoint(
        id="WP-002",
        title="Policy Context",
        objective="Use memory index context",
        acceptance_criteria=["context appears in prompt"],
    )

    prompt = build_execution_prompt(
        waypoint,
        "Spec content",
        Path("project"),
        Checklist(items=["Run tests"]),
        directory_policy_context="- Focus your search in: src, tests",
    )

    assert "Project Memory (Directory Index)" in prompt
    assert "Focus your search in: src, tests" in prompt
