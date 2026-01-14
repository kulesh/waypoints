"""Main module for waypoints."""

import argparse
import logging
import os
import sys
from pathlib import Path

from waypoints.config.paths import get_paths


def setup_logging() -> None:
    """Configure logging to file for debugging."""
    paths = get_paths()
    paths.workspace_config.mkdir(parents=True, exist_ok=True)
    log_file = paths.debug_log

    # Set level from env var, default to INFO
    level = os.environ.get("WAYPOINTS_LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w"),
        ],
    )
    logging.info("Waypoints starting, logging to %s", log_file)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
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

    return parser.parse_args()


def cmd_export(args: argparse.Namespace) -> int:
    """Export a project to generative specification."""
    from waypoints.config.paths import get_paths
    from waypoints.genspec import export_project, export_to_file
    from waypoints.models.project import Project

    paths = get_paths()
    project_path = paths.projects_dir / args.project

    if not project_path.exists():
        print(f"Error: Project '{args.project}' not found", file=sys.stderr)
        print(f"  Looked in: {paths.projects_dir}", file=sys.stderr)
        return 1

    # Load project
    project = Project.load(project_path)
    if project is None:
        print(f"Error: Could not load project '{args.project}'", file=sys.stderr)
        return 1

    # Export
    spec = export_project(project)

    # Determine output path
    output_path = args.output or Path(f"{args.project}.genspec.jsonl")
    export_to_file(spec, output_path)

    print(f"Exported {spec.summary()['total_steps']} steps to {output_path}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Import a generative specification to create a new project."""
    from waypoints.genspec.importer import (
        create_project_from_spec,
        import_from_file,
        validate_spec,
    )

    # Import the spec
    try:
        spec = import_from_file(args.file)
    except FileNotFoundError:
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: Invalid genspec file: {e}", file=sys.stderr)
        return 1

    # Validate
    validation = validate_spec(spec)
    if validation.has_errors:
        print("Validation errors:", file=sys.stderr)
        for error in validation.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    for warning in validation.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    # Determine project name
    name = args.name or f"{spec.source_project} (imported)"

    # Create project
    replay_mode = args.mode == "replay"
    try:
        project = create_project_from_spec(spec, name, replay_mode=replay_mode)
    except ValueError as e:
        print(f"Error creating project: {e}", file=sys.stderr)
        return 1

    print(f"Created project: {project.name}")
    print(f"  Slug: {project.slug}")
    print(f"  Path: {project.get_path()}")

    if replay_mode:
        print("\nProject created with cached artifacts (replay mode).")
        print("Run 'waypoints' to open the TUI and continue from CHART phase.")
    else:
        print("\nProject prepared for regeneration.")
        print("Run the executor to regenerate from prompts.")

    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    """Launch the TUI application."""
    from waypoints.tui.app import WaypointsApp

    app = WaypointsApp()
    app.run()
    return 0


def main() -> None:
    """Entry point for the Waypoints application."""
    args = parse_args()

    # Change to workdir if specified (before logging setup)
    if args.workdir:
        workdir = args.workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        os.chdir(workdir)

    setup_logging()
    logging.info("Working directory: %s", Path.cwd())

    # Route to command handler
    if args.command == "export":
        sys.exit(cmd_export(args))
    elif args.command == "import":
        sys.exit(cmd_import(args))
    else:
        # Default: launch TUI
        sys.exit(cmd_tui(args))


if __name__ == "__main__":
    main()
