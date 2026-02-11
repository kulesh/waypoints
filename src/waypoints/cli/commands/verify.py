"""Verify command for genspec reproducibility checks."""

from __future__ import annotations

import argparse

from waypoints.verify.orchestrator import run_verification


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify genspec reproducibility."""
    return run_verification(
        genspec_dir=args.genspec_dir,
        bootstrap=args.bootstrap,
        skip_fly=args.skip_fly,
        verbose=args.verbose,
    )
