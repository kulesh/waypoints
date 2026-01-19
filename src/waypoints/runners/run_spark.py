"""SPARK phase runner: idea → Q&A dialogue.

Usage:
    # Interactive (prompts for responses)
    echo "Build a blog" | python -m waypoints.runners.run_spark -p my-blog

    # Batch mode (auto-complete after N turns)
    echo "Build a blog" | python -m waypoints.runners.run_spark -p test -t 3

    # Output dialogue to file
    python -m waypoints.runners.run_spark -p my-blog -i "Build a blog" > qa.json

Output:
    JSON object with dialogue history (to stdout)
"""

import argparse
import json
import sys

from waypoints.models import Project
from waypoints.orchestration import JourneyCoordinator


def main() -> int:
    """Run SPARK phase: Q&A dialogue from initial idea."""
    parser = argparse.ArgumentParser(
        description="Run SPARK phase: generate Q&A dialogue from an idea",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--project",
        "-p",
        required=True,
        help="Project name (creates if doesn't exist)",
    )
    parser.add_argument(
        "--idea",
        "-i",
        help="Initial idea (reads from stdin if not provided)",
    )
    parser.add_argument(
        "--turns",
        "-t",
        type=int,
        default=0,
        help="Auto-complete after N turns (0 = interactive mode)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show progress on stderr",
    )

    args = parser.parse_args()

    # Get idea from argument or stdin
    if args.idea:
        idea = args.idea
    elif not sys.stdin.isatty():
        idea = sys.stdin.read().strip()
    else:
        print("Error: Provide --idea or pipe idea to stdin", file=sys.stderr)
        return 1

    if not idea:
        print("Error: Idea cannot be empty", file=sys.stderr)
        return 1

    # Load or create project
    try:
        project = Project.load(args.project)
        if args.verbose:
            print(f"Loaded project: {project.name}", file=sys.stderr)
    except FileNotFoundError:
        project = Project.create(args.project, idea=idea)
        if args.verbose:
            print(f"Created project: {project.name}", file=sys.stderr)

    # Initialize coordinator
    coordinator = JourneyCoordinator(project=project)

    def on_chunk(chunk: str) -> None:
        """Print streaming chunks to stderr in verbose mode."""
        if args.verbose:
            print(chunk, end="", file=sys.stderr, flush=True)

    # Start Q&A dialogue
    if args.verbose:
        print("\n--- Starting Q&A ---", file=sys.stderr)

    coordinator.start_qa_dialogue(idea=idea, on_chunk=on_chunk)

    if args.verbose:
        print("\n", file=sys.stderr)

    # Continue dialogue for specified turns or interactively
    turn = 1
    while True:
        if args.turns > 0 and turn >= args.turns:
            # Batch mode: auto-complete
            break

        if args.turns == 0:
            # Interactive mode: prompt user
            try:
                msg = f"\n[Turn {turn}] Your response (Ctrl+D to finish):"
                print(msg, file=sys.stderr)
                user_input = input()
                if not user_input.strip():
                    continue
            except EOFError:
                break
        else:
            # Batch mode but not yet at limit - shouldn't happen with current logic
            break

        if args.verbose:
            print("\n--- AI Response ---", file=sys.stderr)

        coordinator.continue_qa_dialogue(
            user_response=user_input,
            on_chunk=on_chunk,
        )
        turn += 1

        if args.verbose:
            print("\n", file=sys.stderr)

    # Output dialogue history as JSON
    if coordinator.dialogue_history:
        output = coordinator.dialogue_history.to_dict()
        print(json.dumps(output, indent=2))
    else:
        print("{}", file=sys.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
