#!/usr/bin/env python3
"""Summarize a completed self-host review checklist."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ChecklistSummary:
    """Computed checklist progress summary."""

    completed: int
    total: int

    @property
    def completion_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.completed / self.total


def summarize_checklist(content: str) -> ChecklistSummary:
    """Count markdown checklist items in review content."""
    total = 0
    completed = 0
    for line in content.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("- [ ] "):
            total += 1
        elif stripped.startswith("- [x] "):
            total += 1
            completed += 1
    return ChecklistSummary(completed=completed, total=total)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize self-host review checklist completion.",
    )
    parser.add_argument(
        "checklist",
        type=Path,
        help="Path to a filled review markdown file",
    )
    args = parser.parse_args()

    checklist_content = args.checklist.read_text(encoding="utf-8")
    summary = summarize_checklist(checklist_content)

    payload = asdict(summary)
    payload["completion_rate"] = round(summary.completion_rate, 4)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
