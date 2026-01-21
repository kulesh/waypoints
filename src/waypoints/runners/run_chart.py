"""CHART phase runner: product spec → flight plan.

Usage:
    # From spec file
    cat spec.md | python -m waypoints.runners.run_chart --project my-blog > plan.json

    # Chain from previous phases
    run_spark | run_shape_brief | run_shape_spec | run_chart --project my-blog

Output:
    JSON flight plan (to stdout)
"""
from __future__ import annotations

import argparse
import json
import sys

from waypoints.models import Project
from waypoints.orchestration import JourneyCoordinator


def main() -> int:
    """Run CHART phase: generate flight plan from product spec."""
    parser = argparse.ArgumentParser(
        description="Run CHART phase: generate flight plan from product spec",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--project",
        "-p",
        required=True,
        help="Project name (must exist)",
    )
    parser.add_argument(
        "--input",
        "-i",
        help="Input spec markdown file (reads from stdin if not provided)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show progress on stderr",
    )

    args = parser.parse_args()

    # Read spec from file or stdin
    if args.input:
        with open(args.input) as f:
            spec = f.read()
    elif not sys.stdin.isatty():
        spec = sys.stdin.read()
    else:
        print("Error: Provide --input or pipe spec to stdin", file=sys.stderr)
        return 1

    if not spec.strip():
        print("Error: Spec cannot be empty", file=sys.stderr)
        return 1

    # Load project
    try:
        project = Project.load(args.project)
    except FileNotFoundError:
        print(f"Error: Project not found: {args.project}", file=sys.stderr)
        return 1

    if args.verbose:
        print(f"Loaded project: {project.name}", file=sys.stderr)
        print(f"Spec has {len(spec)} chars", file=sys.stderr)
        print("\n--- Generating Flight Plan ---", file=sys.stderr)

    # Initialize coordinator
    coordinator = JourneyCoordinator(project=project)

    def on_chunk(chunk: str) -> None:
        """Print streaming chunks to stderr in verbose mode."""
        if args.verbose:
            print(chunk, end="", file=sys.stderr, flush=True)

    # Generate flight plan
    flight_plan = coordinator.generate_flight_plan(spec=spec, on_chunk=on_chunk)

    if args.verbose:
        print("\n", file=sys.stderr)
        print(f"Generated {len(flight_plan.waypoints)} waypoints", file=sys.stderr)

    # Output flight plan as JSON to stdout
    print(json.dumps(flight_plan.to_dict(), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
