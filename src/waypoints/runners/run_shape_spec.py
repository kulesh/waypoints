"""SHAPE phase runner: idea brief → product spec.

Usage:
    # From brief file
    cat brief.md | python -m waypoints.runners.run_shape_spec -p my-blog > spec.md

    # Chain from previous phases
    run_spark -p p -t 3 | run_shape_brief -p p | run_shape_spec -p p

Output:
    Markdown product specification (to stdout)
"""
from __future__ import annotations

import argparse
import sys

from waypoints.models import Project
from waypoints.orchestration import JourneyCoordinator


def main() -> int:
    """Run SHAPE phase: generate product spec from idea brief."""
    parser = argparse.ArgumentParser(
        description="Run SHAPE phase: generate product spec from idea brief",
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
        help="Input brief markdown file (reads from stdin if not provided)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show progress on stderr",
    )

    args = parser.parse_args()

    # Read brief from file or stdin
    if args.input:
        with open(args.input) as f:
            brief = f.read()
    elif not sys.stdin.isatty():
        brief = sys.stdin.read()
    else:
        print("Error: Provide --input or pipe brief to stdin", file=sys.stderr)
        return 1

    if not brief.strip():
        print("Error: Brief cannot be empty", file=sys.stderr)
        return 1

    # Load project
    try:
        project = Project.load(args.project)
    except FileNotFoundError:
        print(f"Error: Project not found: {args.project}", file=sys.stderr)
        return 1

    if args.verbose:
        print(f"Loaded project: {project.name}", file=sys.stderr)
        print(f"Brief has {len(brief)} chars", file=sys.stderr)
        print("\n--- Generating Spec ---", file=sys.stderr)

    # Initialize coordinator
    coordinator = JourneyCoordinator(project=project)

    def on_chunk(chunk: str) -> None:
        """Print streaming chunks to stderr in verbose mode."""
        if args.verbose:
            print(chunk, end="", file=sys.stderr, flush=True)

    # Generate spec
    spec = coordinator.generate_product_spec(brief=brief, on_chunk=on_chunk)

    if args.verbose:
        print("\n", file=sys.stderr)

    # Output spec to stdout
    print(spec)

    return 0


if __name__ == "__main__":
    sys.exit(main())
