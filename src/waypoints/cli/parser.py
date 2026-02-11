"""Argument parser construction for Waypoints CLI."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level CLI parser."""
    parser = argparse.ArgumentParser(
        description="Waypoints - AI-native software development"
    )
    parser.add_argument(
        "--workdir",
        "-w",
        type=Path,
        help="Working directory for project artifacts (default: current directory)",
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Export command
    export_parser = subparsers.add_parser(
        "export",
        help="Export project to generative specification",
    )
    export_parser.add_argument(
        "project",
        help="Project slug to export",
    )
    export_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (default: {project}.genspec.jsonl)",
    )
    export_parser.add_argument(
        "--bundle",
        action="store_true",
        help="Export as a genspec bundle zip (default: {project}.genspec.zip)",
    )

    # Import command
    import_parser = subparsers.add_parser(
        "import",
        help="Import generative specification to create new project",
    )
    import_parser.add_argument(
        "file",
        type=Path,
        help="Path to genspec.jsonl file",
    )
    import_parser.add_argument(
        "--name",
        "-n",
        help="Name for the new project (default: from spec)",
    )
    import_parser.add_argument(
        "--mode",
        choices=["replay", "regenerate"],
        default="replay",
        help="Import mode: replay uses cached outputs, regenerate calls LLM",
    )
    import_parser.add_argument(
        "--run",
        action="store_true",
        help="Immediately execute waypoints after import",
    )

    # Run command (headless execution)
    run_parser = subparsers.add_parser(
        "run",
        help="Execute waypoints for a project (headless)",
    )
    run_parser.add_argument(
        "project",
        help="Project slug to execute",
    )
    run_parser.add_argument(
        "--on-error",
        choices=["retry", "skip", "abort"],
        default="abort",
        help="Behavior on waypoint failure (default: abort)",
    )
    run_parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Maximum iterations per waypoint (default: 10)",
    )

    # Compare command (verification)
    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare two artifacts for semantic equivalence",
    )
    compare_parser.add_argument(
        "artifact_a",
        type=Path,
        help="First artifact file (spec or flight plan)",
    )
    compare_parser.add_argument(
        "artifact_b",
        type=Path,
        help="Second artifact file (spec or flight plan)",
    )
    compare_parser.add_argument(
        "--type",
        "-t",
        choices=["spec", "plan"],
        default="spec",
        help="Artifact type (default: spec)",
    )
    compare_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show streaming LLM output",
    )

    # Verify command
    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify genspec reproducibility",
    )
    verify_parser.add_argument(
        "genspec_dir",
        type=Path,
        help="Directory containing genspec artifacts (idea-brief, etc.)",
    )
    verify_parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Create reference from current generation (first run)",
    )
    verify_parser.add_argument(
        "--skip-fly",
        action="store_true",
        help="Skip execution, compare artifacts only",
    )
    verify_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed progress",
    )

    # View command (genspec inspection)
    view_parser = subparsers.add_parser(
        "view",
        help="View a genspec JSONL file or bundle",
    )
    view_parser.add_argument(
        "path",
        type=Path,
        help="Path to genspec.jsonl or .genspec.zip bundle",
    )
    view_parser.add_argument(
        "--steps-limit",
        type=int,
        default=50,
        help="Max steps to show (0 for all)",
    )
    view_parser.add_argument(
        "--no-steps",
        action="store_true",
        help="Hide step timeline",
    )
    view_parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Hide artifact previews",
    )
    view_parser.add_argument(
        "--preview-lines",
        type=int,
        default=8,
        help="Lines per artifact preview (0 to disable)",
    )

    # Memory command
    memory_parser = subparsers.add_parser(
        "memory",
        help="Project memory utilities",
    )
    memory_subparsers = memory_parser.add_subparsers(
        dest="memory_action",
        help="Memory actions",
    )

    refresh_parser = memory_subparsers.add_parser(
        "refresh",
        help="Refresh project memory index from current filesystem",
    )
    refresh_parser.add_argument(
        "project",
        nargs="?",
        help="Project slug to refresh (default: all projects)",
    )
    refresh_parser.add_argument(
        "--all",
        action="store_true",
        help="Refresh memory for all projects",
    )
    refresh_parser.add_argument(
        "--init-overrides",
        action="store_true",
        help="Create policy override template if missing",
    )

    refresh_spec_context_parser = memory_subparsers.add_parser(
        "refresh-spec-context",
        help="Regenerate waypoint spec context summaries and section refs",
    )
    refresh_spec_context_parser.add_argument(
        "project",
        nargs="?",
        help="Project slug to refresh (default: all projects)",
    )
    refresh_spec_context_parser.add_argument(
        "--all",
        action="store_true",
        help="Refresh spec context for all projects",
    )
    refresh_spec_context_parser.add_argument(
        "--only-stale",
        action="store_true",
        help="Only refresh waypoints with missing/stale spec context",
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments from argv (or sys.argv when omitted)."""
    parser = build_parser()
    if argv is None:
        return parser.parse_args()
    return parser.parse_args(list(argv))
