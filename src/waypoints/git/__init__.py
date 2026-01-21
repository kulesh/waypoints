"""Git integration for waypoints.

This module implements the "pilot and dog" architecture:
- Model (pilot): Executes tasks, runs conceptual checklists, produces receipts
- Code (dog): Validates receipts, enforces guardrails, commits if valid

Key components:
- GitService: Core git operations (init, stage, commit, tag)
- ReceiptValidator: Validates checklist receipts before commits
- GitConfig: Configuration for git behavior
- Checklist: Conceptual checklist items for model interpretation
"""
from __future__ import annotations

from waypoints.git.config import Checklist, GitConfig
from waypoints.git.receipt import (
    ChecklistItem,
    ChecklistReceipt,
    ReceiptValidationResult,
    ReceiptValidator,
)
from waypoints.git.service import GitResult, GitService

__all__ = [
    # Service
    "GitService",
    "GitResult",
    # Receipt
    "ChecklistItem",
    "ChecklistReceipt",
    "ReceiptValidator",
    "ReceiptValidationResult",
    # Config
    "GitConfig",
    "Checklist",
]
