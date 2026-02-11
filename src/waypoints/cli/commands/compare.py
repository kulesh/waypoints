"""Compare command for semantic artifact comparison."""

from __future__ import annotations

import argparse
import json
import sys

from waypoints.verify.compare import compare_flight_plans, compare_specs


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two artifacts for semantic equivalence."""
    if not args.artifact_a.exists():
        print(f"Error: File not found: {args.artifact_a}", file=sys.stderr)
        return 1
    if not args.artifact_b.exists():
        print(f"Error: File not found: {args.artifact_b}", file=sys.stderr)
        return 1

    artifact_a = args.artifact_a.read_text()
    artifact_b = args.artifact_b.read_text()

    if args.type == "spec":
        result = compare_specs(artifact_a, artifact_b, verbose=args.verbose)
    else:
        result = compare_flight_plans(artifact_a, artifact_b, verbose=args.verbose)

    print(json.dumps(result.to_dict(), indent=2))

    if result.verdict.value == "equivalent":
        return 0
    if result.verdict.value == "different":
        return 1
    return 2
