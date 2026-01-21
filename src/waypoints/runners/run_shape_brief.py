"""SHAPE phase runner: Q&A dialogue → idea brief.

Usage:
    # From dialogue JSON
    cat qa.json | python -m waypoints.runners.run_shape_brief -p my-blog > brief.md

    # Chain from spark
    run_spark -p my-blog -t 3 | run_shape_brief -p my-blog

Output:
    Markdown idea brief (to stdout)
"""
from __future__ import annotations

import argparse
import json
import sys

from waypoints.models import DialogueHistory, Project
from waypoints.orchestration import JourneyCoordinator


def main() -> int:
    """Run SHAPE phase: generate idea brief from Q&A dialogue."""
    parser = argparse.ArgumentParser(
        description="Run SHAPE phase: generate idea brief from Q&A dialogue",
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
        help="Input dialogue JSON file (reads from stdin if not provided)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show progress on stderr",
    )

    args = parser.parse_args()

    # Read dialogue from file or stdin
    if args.input:
        with open(args.input) as f:
            dialogue_data = json.load(f)
    elif not sys.stdin.isatty():
        dialogue_data = json.load(sys.stdin)
    else:
        print("Error: Provide --input or pipe dialogue JSON to stdin", file=sys.stderr)
        return 1

    # Parse dialogue history
    try:
        history = DialogueHistory.from_dict(dialogue_data)
    except (KeyError, ValueError) as e:
        print(f"Error: Invalid dialogue format: {e}", file=sys.stderr)
        return 1

    if not history.messages:
        print("Error: Dialogue history is empty", file=sys.stderr)
        return 1

    # Load project
    try:
        project = Project.load(args.project)
    except FileNotFoundError:
        print(f"Error: Project not found: {args.project}", file=sys.stderr)
        return 1

    if args.verbose:
        print(f"Loaded project: {project.name}", file=sys.stderr)
        print(f"Dialogue has {len(history.messages)} messages", file=sys.stderr)
        print("\n--- Generating Brief ---", file=sys.stderr)

    # Initialize coordinator
    coordinator = JourneyCoordinator(project=project)

    def on_chunk(chunk: str) -> None:
        """Print streaming chunks to stderr in verbose mode."""
        if args.verbose:
            print(chunk, end="", file=sys.stderr, flush=True)

    # Generate brief
    brief = coordinator.generate_idea_brief(history=history, on_chunk=on_chunk)

    if args.verbose:
        print("\n", file=sys.stderr)

    # Output brief to stdout
    print(brief)

    return 0


if __name__ == "__main__":
    sys.exit(main())
