"""TUI launch command."""

from __future__ import annotations

import argparse

from waypoints.tui.app import WaypointsApp


def cmd_tui(args: argparse.Namespace) -> int:
    """Launch the TUI application."""
    del args
    app = WaypointsApp()
    app.run()
    return 0
