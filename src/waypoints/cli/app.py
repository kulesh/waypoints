"""CLI orchestration and command routing."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from waypoints.cli.commands import (
    cmd_compare,
    cmd_export,
    cmd_import,
    cmd_memory,
    cmd_run,
    cmd_tui,
    cmd_verify,
    cmd_view,
)
from waypoints.cli.parser import parse_args

logger = logging.getLogger(__name__)


def dispatch(args: argparse.Namespace) -> int:
    """Route parsed args to the correct command handler."""
    command_handlers: dict[str, Callable[[argparse.Namespace], int]] = {
        "export": cmd_export,
        "import": cmd_import,
        "run": cmd_run,
        "compare": cmd_compare,
        "view": cmd_view,
        "verify": cmd_verify,
        "memory": cmd_memory,
    }

    if args.command is None:
        return cmd_tui(args)

    handler = command_handlers.get(args.command)
    if handler is None:
        return cmd_tui(args)

    return handler(args)


def run(
    argv: Sequence[str] | None = None,
    *,
    configure_logging: Callable[[], None] | None = None,
) -> int:
    """Parse args, apply shared CLI setup, and execute command."""
    args = parse_args(argv)

    if args.workdir:
        workdir = args.workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        os.chdir(workdir)

    if configure_logging is not None:
        configure_logging()

    logger.info("Working directory: %s", Path.cwd())
    return dispatch(args)
