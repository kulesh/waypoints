"""Main module for waypoints."""

import argparse
import logging
import os
from pathlib import Path

from waypoints.tui.app import WaypointsApp


def setup_logging() -> None:
    """Configure logging to file for debugging."""
    # Log to .waypoints/debug.log in current directory
    log_dir = Path(".waypoints")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "debug.log"

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
    return parser.parse_args()


def main() -> None:
    """Entry point for the Waypoints TUI application."""
    args = parse_args()

    # Change to workdir if specified (before logging setup)
    if args.workdir:
        workdir = args.workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        os.chdir(workdir)

    setup_logging()
    logging.info("Working directory: %s", Path.cwd())

    app = WaypointsApp()
    app.run()


if __name__ == "__main__":
    main()
