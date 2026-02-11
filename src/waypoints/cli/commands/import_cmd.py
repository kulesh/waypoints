"""Import command for genspec artifacts."""

from __future__ import annotations

import argparse
import sys

from waypoints.genspec.importer import (
    create_project_from_spec,
    import_from_file,
    validate_spec,
)

from .run import cmd_run


def cmd_import(args: argparse.Namespace) -> int:
    """Import a generative specification to create a new project."""
    try:
        spec = import_from_file(args.file)
    except FileNotFoundError:
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: Invalid genspec file: {e}", file=sys.stderr)
        return 1

    validation = validate_spec(spec)
    if validation.has_errors:
        print("Validation errors:", file=sys.stderr)
        for error in validation.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    for warning in validation.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    name = args.name or f"{spec.source_project} (imported)"
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

    if args.run:
        print("\nStarting headless execution...")
        run_args = argparse.Namespace(
            project=project.slug,
            on_error="abort",
            max_iterations=10,
        )
        return cmd_run(run_args)

    return 0
