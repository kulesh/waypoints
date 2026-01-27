"""FLY phase runner: flight plan → execution log.

Usage:
    # Execute all pending waypoints
    python -m waypoints.runners.run_fly --project my-blog

    # Execute specific waypoint
    python -m waypoints.runners.run_fly --project my-blog --waypoint WP-001

    # Skip waypoints requiring intervention
    python -m waypoints.runners.run_fly --project my-blog --skip-intervention

Output:
    JSONL execution events (to stdout)
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from waypoints.fly.executor import ExecutionResult
from waypoints.fly.intervention import InterventionNeededError
from waypoints.models import Project, WaypointStatus
from waypoints.models.flight_plan import FlightPlanReader
from waypoints.orchestration import JourneyCoordinator


def log_event(event_type: str, data: dict[str, Any]) -> None:
    """Write an event to stdout as JSONL."""
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "type": event_type,
        **data,
    }
    print(json.dumps(event), flush=True)


async def run_execution(args: argparse.Namespace) -> int:
    """Run waypoint execution asynchronously."""
    # Load project
    try:
        project = Project.load(args.project)
    except FileNotFoundError:
        print(f"Error: Project not found: {args.project}", file=sys.stderr)
        return 1

    # Load flight plan
    flight_plan = FlightPlanReader.load(project)
    if not flight_plan:
        print("Error: No flight plan found for project", file=sys.stderr)
        return 1

    if args.verbose:
        print(f"Loaded project: {project.name}", file=sys.stderr)
        n_waypoints = len(flight_plan.waypoints)
        print(f"Flight plan has {n_waypoints} waypoints", file=sys.stderr)

    # Initialize coordinator
    coordinator = JourneyCoordinator(project=project, flight_plan=flight_plan)

    # Determine waypoints to execute
    if args.waypoint:
        waypoint = flight_plan.get_waypoint(args.waypoint)
        if not waypoint:
            print(f"Error: Waypoint not found: {args.waypoint}", file=sys.stderr)
            return 1
        waypoints_to_execute = [waypoint]
    else:
        waypoints_to_execute = [
            wp
            for wp in flight_plan.waypoints
            if wp.status == WaypointStatus.PENDING
            and not flight_plan.is_epic(wp.id)
        ]

    if not waypoints_to_execute:
        log_event("no_work", {"message": "No pending waypoints to execute"})
        return 0

    log_event(
        "execution_started",
        {"waypoint_count": len(waypoints_to_execute)},
    )

    # Execute each waypoint
    errors = 0
    if args.waypoint:
        loop_targets = waypoints_to_execute
    else:
        loop_targets = []
        while True:
            next_wp = coordinator.select_next_waypoint()
            if not next_wp:
                break
            if next_wp.status != WaypointStatus.PENDING:
                break
            loop_targets.append(next_wp)

    for waypoint in loop_targets:
        log_event(
            "waypoint_started",
            {"waypoint_id": waypoint.id, "title": waypoint.title},
        )

        try:
            result = await coordinator.execute_waypoint(
                waypoint=waypoint,
                max_iterations=args.max_iterations,
                host_validations_enabled=args.host_validations_enabled,
            )

            if result == ExecutionResult.SUCCESS:
                log_event(
                    "waypoint_completed",
                    {"waypoint_id": waypoint.id, "status": "success"},
                )
                coordinator.handle_execution_result(waypoint, result)
            else:
                log_event(
                    "waypoint_failed",
                    {"waypoint_id": waypoint.id, "status": "failed"},
                )
                errors += 1

        except InterventionNeededError as e:
            log_event(
                "intervention_needed",
                {
                    "waypoint_id": waypoint.id,
                    "intervention_type": e.intervention.type.value,
                    "error_summary": e.intervention.error_summary,
                },
            )
            if args.skip_intervention:
                if args.verbose:
                    print(
                        f"Skipping {waypoint.id}: intervention required",
                        file=sys.stderr,
                    )
                continue
            else:
                print(
                    f"Error: Intervention required for {waypoint.id}",
                    file=sys.stderr,
                )
                errors += 1
                if not args.continue_on_error:
                    break

        except Exception as e:
            log_event(
                "waypoint_error",
                {"waypoint_id": waypoint.id, "error": str(e)},
            )
            errors += 1
            if not args.continue_on_error:
                break

    log_event(
        "execution_finished",
        {"total": len(waypoints_to_execute), "errors": errors},
    )

    return 1 if errors > 0 else 0


def main() -> int:
    """Run FLY phase: execute waypoints from flight plan."""
    parser = argparse.ArgumentParser(
        description="Run FLY phase: execute waypoints from flight plan",
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
        "--waypoint",
        "-w",
        help="Execute specific waypoint by ID",
    )
    parser.add_argument(
        "--max-iterations",
        "-m",
        type=int,
        default=10,
        help="Maximum execution iterations per waypoint (default: 10)",
    )
    parser.add_argument(
        "--no-host-validations",
        dest="host_validations_enabled",
        action="store_false",
        help="Disable host validations (LLM-as-judge only)",
    )
    parser.add_argument(
        "--skip-intervention",
        action="store_true",
        help="Skip waypoints that require intervention",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue executing remaining waypoints on error",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show progress on stderr",
    )

    parser.set_defaults(host_validations_enabled=True)

    args = parser.parse_args()

    return asyncio.run(run_execution(args))


if __name__ == "__main__":
    sys.exit(main())
