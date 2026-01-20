"""Structured execution protocol reporting for FLY phase."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ExecutionStage(str, Enum):
    """Ordered execution stages for waypoint completion."""

    ANALYZE = "analyze"
    PLAN = "plan"
    TEST = "test"
    CODE = "code"
    RUN = "run"
    FIX = "fix"
    LINT = "lint"
    REPORT = "report"


@dataclass(frozen=True)
class StageReport:
    """Structured report for a single execution stage."""

    stage: ExecutionStage
    success: bool
    output: str
    artifacts: list[str]
    next_stage: ExecutionStage | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageReport":
        stage_value = data.get("stage")
        if stage_value is None:
            raise ValueError("Stage report missing 'stage'")
        stage = ExecutionStage(stage_value)

        next_stage_value = data.get("next_stage")
        next_stage = (
            ExecutionStage(next_stage_value) if next_stage_value else None
        )

        return cls(
            stage=stage,
            success=bool(data.get("success")),
            output=str(data.get("output", "")),
            artifacts=list(data.get("artifacts", [])),
            next_stage=next_stage,
        )


STAGE_REPORT_PATTERN = re.compile(
    r"<execution-stage>\s*(\{.*?\})\s*</execution-stage>",
    re.DOTALL,
)


def parse_stage_reports(text: str) -> list[StageReport]:
    """Parse structured stage reports from model output."""
    reports: list[StageReport] = []
    for match in STAGE_REPORT_PATTERN.findall(text):
        try:
            data = json.loads(match)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            reports.append(StageReport.from_dict(data))
        except (ValueError, KeyError):
            continue
    return reports
