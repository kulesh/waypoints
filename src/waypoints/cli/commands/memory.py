"""Memory command for project memory maintenance."""

from __future__ import annotations

import argparse
import sys

from waypoints.config.project_root import get_projects_root
from waypoints.memory import (
    load_or_build_project_memory,
    waypoint_memory_dir,
    write_default_policy_overrides,
)
from waypoints.models.flight_plan import FlightPlanReader, FlightPlanWriter
from waypoints.models.project import Project
from waypoints.spec import (
    load_project_spec_text,
    refresh_flight_plan_spec_context,
)


def cmd_memory(args: argparse.Namespace) -> int:
    """Manage project memory artifacts."""
    if args.memory_action not in {"refresh", "refresh-spec-context"}:
        print("Error: Unknown memory action", file=sys.stderr)
        return 2

    projects: list[Project]
    if args.all or not args.project:
        projects = Project.list_all()
        if not projects:
            print("No projects found to refresh.")
            return 0
    else:
        projects_root = get_projects_root()
        project_path = projects_root / args.project
        if not project_path.exists():
            print(f"Error: Project '{args.project}' not found", file=sys.stderr)
            print(f"  Looked in: {projects_root}", file=sys.stderr)
            return 1
        projects = [Project.load(args.project)]

    for project in projects:
        project_root = project.get_path()
        if args.memory_action == "refresh":
            memory = load_or_build_project_memory(project_root, force_refresh=True)
            if args.init_overrides:
                write_default_policy_overrides(project_root)
            waypoint_records = 0
            waypoint_root = waypoint_memory_dir(project_root)
            if waypoint_root.exists():
                waypoint_records = len(list(waypoint_root.glob("*.json")))
            print(
                (
                    f"{project.slug}: refreshed memory "
                    f"(focus={len(memory.index.focus_top_level_dirs)} "
                    f"ignored={len(memory.index.ignored_top_level_dirs)} "
                    f"blocked={len(memory.index.blocked_top_level_dirs)} "
                    f"waypoint_records={waypoint_records})"
                )
            )
            continue

        spec_text = load_project_spec_text(project_root)
        if not spec_text.strip():
            print(f"{project.slug}: skipped (no product spec found)")
            continue

        flight_plan = FlightPlanReader.load(project)
        if flight_plan is None:
            print(f"{project.slug}: skipped (no flight plan found)")
            continue

        stats = refresh_flight_plan_spec_context(
            flight_plan,
            spec_text,
            only_stale=args.only_stale,
        )
        if stats.regenerated_waypoints > 0:
            FlightPlanWriter(project).save(flight_plan)
        print(
            (
                f"{project.slug}: refreshed waypoint spec context "
                f"(total={stats.total_waypoints} "
                f"stale_or_missing={stats.stale_or_missing_waypoints} "
                f"updated={stats.regenerated_waypoints} "
                f"unchanged={stats.unchanged_waypoints} "
                f"hash={stats.spec_hash})"
            )
        )
    return 0
