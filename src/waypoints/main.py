"""Main module for waypoints."""

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


def main() -> None:
    """Entry point for the Waypoints TUI application."""
    setup_logging()
    app = WaypointsApp()
    app.run()


if __name__ == "__main__":
    main()
