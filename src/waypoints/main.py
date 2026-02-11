"""Main module and compatibility exports for Waypoints CLI."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from waypoints.cli.app import run as run_cli
from waypoints.cli.commands.compare import cmd_compare as _cmd_compare
from waypoints.cli.commands.export import cmd_export as _cmd_export
from waypoints.cli.commands.import_cmd import cmd_import as _cmd_import
from waypoints.cli.commands.memory import cmd_memory as _cmd_memory
from waypoints.cli.commands.run import cmd_run as _cmd_run
from waypoints.cli.commands.tui import cmd_tui as _cmd_tui
from waypoints.cli.commands.verify import cmd_verify as _cmd_verify
from waypoints.cli.commands.view import cmd_view as _cmd_view
from waypoints.cli.parser import parse_args as _parse_args
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

    level = os.environ.get("WAYPOINTS_LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, mode="w")],
    )
    logging.info("Waypoints starting, logging to %s", log_file)
    projects_root = get_projects_root()
    source = (
        "settings.project_directory override"
        if is_projects_root_overridden()
        else "workspace .waypoints/projects"
    )
    logging.info("Projects root: %s (%s)", projects_root, source)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Compatibility wrapper for legacy imports."""
    return _parse_args(argv)


def cmd_export(args: argparse.Namespace) -> int:
    return _cmd_export(args)


def cmd_import(args: argparse.Namespace) -> int:
    return _cmd_import(args)


def cmd_run(args: argparse.Namespace) -> int:
    return _cmd_run(args)


def cmd_compare(args: argparse.Namespace) -> int:
    return _cmd_compare(args)


def cmd_view(args: argparse.Namespace) -> int:
    return _cmd_view(args)


def cmd_verify(args: argparse.Namespace) -> int:
    return _cmd_verify(args)


def cmd_memory(args: argparse.Namespace) -> int:
    return _cmd_memory(args)


def cmd_tui(args: argparse.Namespace) -> int:
    return _cmd_tui(args)


def main() -> None:
    """Entry point for the Waypoints application."""
    exit_code = run_cli(sys.argv[1:], configure_logging=setup_logging)
    sys.exit(exit_code)


__all__ = [
    "cmd_compare",
    "cmd_export",
    "cmd_import",
    "cmd_memory",
    "cmd_run",
    "cmd_tui",
    "cmd_verify",
    "cmd_view",
    "main",
    "parse_args",
    "setup_logging",
]


if __name__ == "__main__":
    main()
