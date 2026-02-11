"""Headless waypoint execution command."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from waypoints.cli.context import load_project_or_error
from waypoints.orchestration.headless_fly import execute_waypoint_with_coordinator


def cmd_run(args: argparse.Namespace) -> int:
    """Execute waypoints for a project (headless mode)."""
    from waypoints.models.flight_plan import FlightPlanReader
    from waypoints.models.waypoint import WaypointStatus
    from waypoints.orchestration.coordinator import JourneyCoordinator

    project = load_project_or_error(args.project)
    if project is None:
        return 1

    flight_plan = FlightPlanReader.load(project)
    if flight_plan is None:
        print("Error: No flight plan found for this project", file=sys.stderr)
        return 1

    print(f"Project: {project.name}")
    print(f"Waypoints: {len(flight_plan.waypoints)}")
    print()

    coordinator = JourneyCoordinator(project, flight_plan)
    coordinator.reset_stale_in_progress()

    completed = 0
    failed = 0
    skipped = 0

    while True:
        include_failed = args.on_error == "retry"
        waypoint = coordinator.select_next_waypoint(include_failed=include_failed)
        if waypoint is None:
            break

        print(f"Executing: {waypoint.id} - {waypoint.title}")
        outcome = asyncio.run(
            execute_waypoint_with_coordinator(
                coordinator,
                waypoint,
                max_iterations=args.max_iterations,
                host_validations_enabled=True,
            )
        )

        if outcome.kind == "success":
            completed += 1
            print("  ✓ Completed")
            if outcome.next_action and outcome.next_action.action == "complete":
                break
            continue

        if outcome.kind == "failed":
            failed += 1
            print("  ✗ Failed")
            if args.on_error == "abort":
                print("\nAborting due to failure (--on-error=abort)")
                break
            if args.on_error == "skip":
                print("  Skipping to next waypoint")
                continue
            if outcome.next_action and outcome.next_action.action == "complete":
                break
            continue

        if outcome.kind == "intervention":
            intervention = outcome.intervention
            msg = (
                intervention.error_summary
                if intervention is not None
                else "Unknown intervention"
            )
            print(f"  ⚠ Intervention needed: {msg}", file=sys.stderr)
            if args.on_error == "abort":
                print("\nAborting due to intervention (--on-error=abort)")
                return 2
            if args.on_error == "skip":
                print("  Skipping to next waypoint")
                skipped += 1
                coordinator.mark_waypoint_status(waypoint, WaypointStatus.SKIPPED)
                continue
            break

        if outcome.error is not None:
            print(f"  ✗ Error: {outcome.error}", file=sys.stderr)
            logging.error("Waypoint execution error", exc_info=outcome.error)
            if args.on_error == "abort":
                return 1
            if args.on_error == "skip":
                skipped += 1
                continue
            break

    print()
    print(f"Summary: {completed} completed, {failed} failed, {skipped} skipped")

    if failed > 0:
        return 1
    return 0
