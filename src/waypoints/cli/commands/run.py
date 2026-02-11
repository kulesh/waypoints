"""Headless waypoint execution command."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from waypoints.cli.context import load_project_or_error


def cmd_run(args: argparse.Namespace) -> int:
    """Execute waypoints for a project (headless mode)."""
    from waypoints.fly.executor import ExecutionResult
    from waypoints.fly.intervention import InterventionNeededError
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
                if args.on_error == "skip":
                    print("  Skipping to next waypoint")
                    continue

            if action.action == "complete":
                break

        except InterventionNeededError as e:
            msg = e.intervention.error_summary
            print(f"  ⚠ Intervention needed: {msg}", file=sys.stderr)
            coordinator.mark_waypoint_status(waypoint, WaypointStatus.FAILED)
            if args.on_error == "abort":
                print("\nAborting due to intervention (--on-error=abort)")
                return 2
            if args.on_error == "skip":
                print("  Skipping to next waypoint")
                skipped += 1
                coordinator.mark_waypoint_status(waypoint, WaypointStatus.SKIPPED)
                continue
            break

        except Exception as e:
            print(f"  ✗ Error: {e}", file=sys.stderr)
            logging.exception("Waypoint execution error")
            coordinator.mark_waypoint_status(waypoint, WaypointStatus.FAILED)
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
