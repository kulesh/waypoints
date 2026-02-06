"""Flight test runner for Waypoints reference projects."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SmokeResult:
    """Outcome of smoke test execution."""

    ran: bool
    exit_code: int | None = None
    log_path: Path | None = None


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")


def _read_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def _run_smoke_test(script: Path, project_path: Path, results_dir: Path) -> SmokeResult:
    log_path = results_dir / "smoke_test.log"
    result = subprocess.run(
        ["bash", str(script)],
        cwd=project_path,
        capture_output=True,
        text=True,
        check=False,
    )
    log_path.write_text(
        f"$ bash {script}\n\n{result.stdout}\n{result.stderr}",
        encoding="utf-8",
    )
    return SmokeResult(ran=True, exit_code=result.returncode, log_path=log_path)


def _write_meta(results_dir: Path, data: dict[str, object]) -> None:
    meta_path = results_dir / "meta.json"
    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def run(flight_test_dir: Path, project_path: Path, *, skip_smoke: bool) -> int:
    input_dir = flight_test_dir / "input"
    expected_dir = flight_test_dir / "expected"

    idea_path = input_dir / "idea.txt"
    min_files_path = expected_dir / "min_files.txt"
    smoke_script = expected_dir / "smoke_test.sh"

    if not idea_path.exists():
        raise FileNotFoundError(f"Missing idea file: {idea_path}")
    if not min_files_path.exists():
        raise FileNotFoundError(f"Missing min_files file: {min_files_path}")
    if not project_path.exists():
        raise FileNotFoundError(f"Project path not found: {project_path}")

    results_dir = flight_test_dir / "results" / _timestamp()
    started_at = datetime.now(UTC)
    results_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy(idea_path, results_dir / "idea.txt")

    required_files = _read_lines(min_files_path)
    missing = [
        str(path)
        for path in required_files
        if not (project_path / path).exists()
    ]

    smoke_result = SmokeResult(ran=False)
    if smoke_script.exists() and not skip_smoke:
        smoke_result = _run_smoke_test(smoke_script, project_path, results_dir)

    success = not missing and (
        not smoke_result.ran or smoke_result.exit_code == 0
    )

    log_path_value = (
        str(smoke_result.log_path) if smoke_result.log_path else None
    )

    _write_meta(
        results_dir,
        {
            "flight_test": flight_test_dir.name,
            "project_path": str(project_path),
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "required_files": required_files,
            "missing_files": missing,
            "smoke_test": {
                "ran": smoke_result.ran,
                "exit_code": smoke_result.exit_code,
                "log_path": log_path_value,
            },
            "success": success,
        },
    )

    summary = "PASS" if success else "FAIL"
    print(f"{flight_test_dir.name}: {summary}")
    if missing:
        print("Missing files:")
        for path in missing:
            print(f"  - {path}")
    if smoke_result.ran and smoke_result.exit_code != 0:
        message = (
            f"Smoke test failed (exit {smoke_result.exit_code}). "
            f"See {smoke_result.log_path}"
        )
        print(message)

    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Waypoints flight test.")
    parser.add_argument("flight_test", type=Path, help="Path to flight test directory")
    parser.add_argument(
        "--project-path",
        type=Path,
        required=True,
        help="Path to generated project to validate",
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip running smoke_test.sh",
    )

    args = parser.parse_args()
    return run(args.flight_test, args.project_path, skip_smoke=args.skip_smoke)


if __name__ == "__main__":
    raise SystemExit(main())
