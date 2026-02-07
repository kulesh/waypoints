"""Flight test suite discovery and smoke-test execution."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

CASE_DIR_PATTERN = re.compile(r"^L(?P<level>\d+)-(?P<slug>[a-z0-9][a-z0-9-]*)$")
LEVEL_TOKEN_PATTERN = re.compile(r"^L(?P<level>\d+)$")
LEVEL_RANGE_PATTERN = re.compile(r"^L(?P<start>\d+)-L(?P<end>\d+)$")


class FlightTestStatus(str, Enum):
    """Outcome state for a flight test case run."""

    PLANNED = "planned"
    SKIPPED = "skipped"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class FlightTestCase:
    """A single discovered flight test case."""

    case_id: str
    level: int
    slug: str
    path: Path
    idea_file: Path
    smoke_test_script: Path

    @property
    def project_dir_name(self) -> str:
        """Conventional generated project folder name for this case."""
        return self.case_id


@dataclass(frozen=True, slots=True)
class FlightTestResult:
    """Execution result for a flight test case."""

    case: FlightTestCase
    status: FlightTestStatus
    message: str
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""


def parse_level_selector(selector: str) -> set[int]:
    """Parse level selector tokens like ``L0`` or ``L0-L3,L5``.

    Args:
        selector: Comma-separated level tokens.

    Returns:
        Set of numeric levels to include.

    Raises:
        ValueError: If any token is malformed.
    """
    levels: set[int] = set()
    for raw_token in selector.split(","):
        token = raw_token.strip()
        if not token:
            continue

        if match := LEVEL_TOKEN_PATTERN.fullmatch(token):
            levels.add(int(match.group("level")))
            continue

        if match := LEVEL_RANGE_PATTERN.fullmatch(token):
            start = int(match.group("start"))
            end = int(match.group("end"))
            if end < start:
                raise ValueError(f"Invalid level range: {token}")
            for level in range(start, end + 1):
                levels.add(level)
            continue

        raise ValueError(f"Invalid level token: {token}")

    if not levels:
        raise ValueError("No levels selected")
    return levels


def discover_flight_tests(suite_root: Path) -> list[FlightTestCase]:
    """Discover flight test cases under a suite root directory."""
    if not suite_root.exists():
        return []

    cases: list[FlightTestCase] = []
    for candidate in sorted(suite_root.iterdir()):
        if not candidate.is_dir():
            continue
        match = CASE_DIR_PATTERN.fullmatch(candidate.name)
        if not match:
            continue

        level = int(match.group("level"))
        case = FlightTestCase(
            case_id=candidate.name,
            level=level,
            slug=match.group("slug"),
            path=candidate,
            idea_file=candidate / "input" / "idea.txt",
            smoke_test_script=candidate / "expected" / "smoke_test.sh",
        )
        cases.append(case)

    return cases


def validate_flight_test_case(case: FlightTestCase) -> list[str]:
    """Return validation issues for a flight test case definition."""
    issues: list[str] = []

    if not case.idea_file.exists():
        issues.append(f"missing idea file: {case.idea_file}")
    elif not case.idea_file.read_text(encoding="utf-8").strip():
        issues.append(f"empty idea file: {case.idea_file}")

    if not case.smoke_test_script.exists():
        issues.append(f"missing smoke test script: {case.smoke_test_script}")

    return issues


def execute_flight_tests(
    cases: list[FlightTestCase],
    *,
    generated_projects_root: Path,
    execute: bool,
    timeout_seconds: int,
) -> list[FlightTestResult]:
    """Execute (or plan) smoke tests for discovered cases."""
    results: list[FlightTestResult] = []

    for case in cases:
        validation_issues = validate_flight_test_case(case)
        if validation_issues:
            results.append(
                FlightTestResult(
                    case=case,
                    status=FlightTestStatus.ERROR,
                    message="; ".join(validation_issues),
                )
            )
            continue

        if not execute:
            results.append(
                FlightTestResult(
                    case=case,
                    status=FlightTestStatus.PLANNED,
                    message="planned (use --execute to run smoke test)",
                )
            )
            continue

        project_path = generated_projects_root / case.project_dir_name
        if not project_path.exists():
            results.append(
                FlightTestResult(
                    case=case,
                    status=FlightTestStatus.SKIPPED,
                    message=f"generated project not found: {project_path}",
                )
            )
            continue

        env = {
            **os.environ,
            "WAYPOINTS_FLIGHT_TEST_ID": case.case_id,
            "WAYPOINTS_FLIGHT_TEST_LEVEL": f"L{case.level}",
            "WAYPOINTS_FLIGHT_TEST_PROJECT": str(project_path),
        }

        try:
            completed = subprocess.run(
                ["bash", str(case.smoke_test_script)],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            results.append(
                FlightTestResult(
                    case=case,
                    status=FlightTestStatus.ERROR,
                    message=f"timed out after {timeout_seconds}s",
                )
            )
            continue
        except OSError as exc:
            results.append(
                FlightTestResult(
                    case=case,
                    status=FlightTestStatus.ERROR,
                    message=f"failed to execute smoke test: {exc}",
                )
            )
            continue

        status = (
            FlightTestStatus.PASSED
            if completed.returncode == 0
            else FlightTestStatus.FAILED
        )
        results.append(
            FlightTestResult(
                case=case,
                status=status,
                message=(
                    "ok" if status == FlightTestStatus.PASSED else "smoke test failed"
                ),
                return_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        )

    return results
