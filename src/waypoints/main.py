"""Main module for waypoints."""

import argparse
import logging
import os
import sys
from pathlib import Path

from waypoints.config.paths import get_paths
from waypoints.config.project_root import (
    get_projects_root,
    is_projects_root_overridden,
)


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
    projects_root = get_projects_root()
    source = (
        "settings.project_directory override"
        if is_projects_root_overridden()
        else "workspace .waypoints/projects"
    )
    logging.info("Projects root: %s (%s)", projects_root, source)


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

    return parser.parse_args()


def cmd_export(args: argparse.Namespace) -> int:
    """Export a project to generative specification."""
    from waypoints.genspec import export_bundle, export_project, export_to_file
    from waypoints.models.project import Project

    projects_root = get_projects_root()
    project_path = projects_root / args.project

    if not project_path.exists():
        print(f"Error: Project '{args.project}' not found", file=sys.stderr)
        print(f"  Looked in: {projects_root}", file=sys.stderr)
        return 1

    # Load project
    project = Project.load(project_path)
    if project is None:
        print(f"Error: Could not load project '{args.project}'", file=sys.stderr)
        return 1

    # Export
    spec = export_project(project)

    step_count = spec.summary()["total_steps"]
    if args.bundle:
        output_path = args.output or Path(f"{args.project}.genspec.zip")
        export_bundle(spec, output_path)
        print(f"Exported bundle with {step_count} steps to {output_path}")
    else:
        output_path = args.output or Path(f"{args.project}.genspec.jsonl")
        export_to_file(spec, output_path)
        print(f"Exported {step_count} steps to {output_path}")
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
        project = create_project_from_spec(args.file, name, replay_mode=replay_mode)
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

    # Optionally run immediately
    if args.run:
        print("\nStarting headless execution...")
        # Create a namespace for cmd_run
        run_args = argparse.Namespace(
            project=project.slug,
            on_error="abort",
            max_iterations=10,
        )
        return cmd_run(run_args)

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Execute waypoints for a project (headless mode)."""
    import asyncio

    from waypoints.fly.executor import ExecutionResult
    from waypoints.fly.intervention import InterventionNeededError
    from waypoints.models.flight_plan import FlightPlanReader
    from waypoints.models.project import Project
    from waypoints.models.waypoint import WaypointStatus
    from waypoints.orchestration.coordinator import JourneyCoordinator

    projects_root = get_projects_root()
    project_path = projects_root / args.project

    if not project_path.exists():
        print(f"Error: Project '{args.project}' not found", file=sys.stderr)
        print(f"  Looked in: {projects_root}", file=sys.stderr)
        return 1

    # Load project
    project = Project.load(project_path)
    if project is None:
        print(f"Error: Could not load project '{args.project}'", file=sys.stderr)
        return 1

    # Load flight plan
    flight_plan = FlightPlanReader.load(project)
    if flight_plan is None:
        print("Error: No flight plan found for this project", file=sys.stderr)
        return 1

    print(f"Project: {project.name}")
    print(f"Waypoints: {len(flight_plan.waypoints)}")
    print()

    # Create coordinator
    coordinator = JourneyCoordinator(project, flight_plan)

    # Reset any stale in-progress waypoints
    coordinator.reset_stale_in_progress()

    # Execution loop
    completed = 0
    failed = 0
    skipped = 0

    while True:
        include_failed = args.on_error == "retry"
        waypoint = coordinator.select_next_waypoint(include_failed=include_failed)
        if waypoint is None:
            break

        print(f"Executing: {waypoint.id} - {waypoint.title}")

        try:
            result = asyncio.run(
                coordinator.execute_waypoint(
                    waypoint,
                    max_iterations=args.max_iterations,
                )
            )

            action = coordinator.handle_execution_result(waypoint, result)

            if result == ExecutionResult.SUCCESS:
                completed += 1
                print("  ✓ Completed")
            elif result == ExecutionResult.FAILED:
                failed += 1
                print("  ✗ Failed")
                if args.on_error == "abort":
                    print("\nAborting due to failure (--on-error=abort)")
                    break
                elif args.on_error == "skip":
                    print("  Skipping to next waypoint")
                    continue
                # retry: will be picked up next iteration

            if action.action == "complete":
                break

        except InterventionNeededError as e:
            msg = e.intervention.error_summary
            print(f"  ⚠ Intervention needed: {msg}", file=sys.stderr)
            if args.on_error == "abort":
                print("\nAborting due to intervention (--on-error=abort)")
                return 2
            elif args.on_error == "skip":
                print("  Skipping to next waypoint")
                skipped += 1
                waypoint.status = WaypointStatus.SKIPPED
                continue
            # retry: pause for now
            break

        except Exception as e:
            print(f"  ✗ Error: {e}", file=sys.stderr)
            logging.exception("Waypoint execution error")
            if args.on_error == "abort":
                return 1
            elif args.on_error == "skip":
                skipped += 1
                continue
            break

    # Summary
    print()
    print(f"Summary: {completed} completed, {failed} failed, {skipped} skipped")

    # Determine exit code
    if failed > 0:
        return 1
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two artifacts for semantic equivalence."""
    import json

    from waypoints.verify.compare import compare_flight_plans, compare_specs

    # Read artifacts
    if not args.artifact_a.exists():
        print(f"Error: File not found: {args.artifact_a}", file=sys.stderr)
        return 1
    if not args.artifact_b.exists():
        print(f"Error: File not found: {args.artifact_b}", file=sys.stderr)
        return 1

    artifact_a = args.artifact_a.read_text()
    artifact_b = args.artifact_b.read_text()

    # Compare based on type
    if args.type == "spec":
        result = compare_specs(artifact_a, artifact_b, verbose=args.verbose)
    else:
        result = compare_flight_plans(artifact_a, artifact_b, verbose=args.verbose)

    # Output result
    print(json.dumps(result.to_dict(), indent=2))

    # Exit code based on verdict
    if result.verdict.value == "equivalent":
        return 0
    elif result.verdict.value == "different":
        return 1
    else:  # uncertain
        return 2


def cmd_view(args: argparse.Namespace) -> int:
    """View a genspec JSONL file or bundle."""
    from waypoints.genspec.viewer import ViewOptions, load_genspec, render_view

    try:
        spec, metadata, checksums = load_genspec(args.path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    options = ViewOptions(
        show_steps=not args.no_steps,
        steps_limit=args.steps_limit,
        show_preview=not args.no_preview,
        preview_lines=args.preview_lines,
    )
    output = render_view(spec, metadata, checksums, options)
    print(output)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify genspec reproducibility."""
    from waypoints.verify.orchestrator import run_verification

    return run_verification(
        genspec_dir=args.genspec_dir,
        bootstrap=args.bootstrap,
        skip_fly=args.skip_fly,
        verbose=args.verbose,
    )


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
    elif args.command == "run":
        sys.exit(cmd_run(args))
    elif args.command == "compare":
        sys.exit(cmd_compare(args))
    elif args.command == "view":
        sys.exit(cmd_view(args))
    elif args.command == "verify":
        sys.exit(cmd_verify(args))
    else:
        # Default: launch TUI
        sys.exit(cmd_tui(args))


if __name__ == "__main__":
    main()
