#!/usr/bin/env python3
"""Discover and execute Waypoints flight tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow direct execution from repository root without installation.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or plan flight test smoke checks.",
    )
    parser.add_argument(
        "--suite-root",
        type=Path,
        default=Path("flight-tests"),
        help="Flight test suite directory (default: flight-tests)",
    )
    parser.add_argument(
        "--level",
        default="L0-L5",
        help="Level selector (e.g. L0, L0-L2, L0-L2,L5)",
    )
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=Path("~/flight-tests/generated").expanduser(),
        help="Generated projects directory (default: ~/flight-tests/generated)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute smoke_test.sh for selected cases (default: plan only)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Per-case smoke test timeout in seconds (default: 600)",
    )
    return parser


def _result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "case_id": result.case.case_id,
        "level": f"L{result.case.level}",
        "status": result.status.value,
        "message": result.message,
        "return_code": result.return_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _render_text(results: list[Any], *, execute: bool) -> str:
    lines: list[str] = []
    mode = "execute" if execute else "plan"
    lines.append(f"Flight test mode: {mode}")
    lines.append(f"Selected cases: {len(results)}")
    lines.append("")

    for result in results:
        lines.append(
            f"[{result.status.value:7}] {result.case.case_id} - {result.message}"
        )

    return "\n".join(lines)


def _exit_code(results: list[Any]) -> int:
    if any(result.status.value in {"failed", "error"} for result in results):
        return 1
    return 0


def main() -> int:
    from waypoints.flight_tests import (
        discover_flight_tests,
        execute_flight_tests,
        parse_level_selector,
    )

    parser = _build_parser()
    args = parser.parse_args()

    try:
        selected_levels = parse_level_selector(args.level)
    except ValueError as exc:
        parser.error(str(exc))

    discovered = discover_flight_tests(args.suite_root)
    selected = [case for case in discovered if case.level in selected_levels]

    results = execute_flight_tests(
        selected,
        generated_projects_root=args.projects_root,
        execute=args.execute,
        timeout_seconds=args.timeout_seconds,
    )

    if args.json:
        payload = {
            "suite_root": str(args.suite_root),
            "projects_root": str(args.projects_root),
            "execute": args.execute,
            "levels": sorted(selected_levels),
            "results": [_result_to_dict(result) for result in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(_render_text(results, execute=args.execute))

    if not selected:
        return 1

    return _exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
