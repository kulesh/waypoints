"""GenSpec Verification System.

Tools for verifying genspec reproducibility and functional equivalence.

Components:
- compare: Semantic comparison tool (LLM judge)
- testgen: Test generation from acceptance criteria
- orchestrator: Full verification pipeline

Usage:
    waypoints verify ./my-genspec --bootstrap  # Create reference
    waypoints verify ./my-genspec              # Compare to reference
    waypoints compare spec-a.md spec-b.md      # Direct comparison
"""
from __future__ import annotations

from waypoints.verify.models import (
    ComparisonResult,
    ComparisonVerdict,
    VerificationReport,
    VerificationStep,
)

__all__ = [
    "ComparisonResult",
    "ComparisonVerdict",
    "VerificationReport",
    "VerificationStep",
]
