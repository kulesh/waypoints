"""Export command for genspec artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from waypoints.cli.context import load_project_or_error
from waypoints.genspec import export_bundle, export_project, export_to_file


def cmd_export(args: argparse.Namespace) -> int:
    """Export a project to generative specification."""
    project = load_project_or_error(args.project)
    if project is None:
        return 1

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
