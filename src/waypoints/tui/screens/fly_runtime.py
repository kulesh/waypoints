"""Runtime helper constructs for Fly screen behavior."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from waypoints.runtime import TimeoutDomain, get_command_runner


class ExecutionState(Enum):
    """State of waypoint execution."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSE_PENDING = "pause_pending"  # Pause requested, finishing current waypoint
    PAUSED = "paused"
    DONE = "done"
    INTERVENTION = "intervention"


def get_git_status_summary(project_path: Path) -> str:
    """Get git status with colored indicator: 'branch [color]●[/] N changed'."""
    try:
        runner = get_command_runner()
        # Get current branch
        branch_result = runner.run(
            command=["git", "branch", "--show-current"],
            domain=TimeoutDomain.UI_GIT_PROBE,
            cwd=project_path,
        )
        if branch_result.effective_exit_code != 0:
            return ""  # Not a git repo
        branch = branch_result.stdout.strip() or "HEAD"

        # Get status (use -uall to show individual files in untracked directories)
        status_result = runner.run(
            command=["git", "status", "--porcelain", "-uall"],
            domain=TimeoutDomain.UI_GIT_PROBE,
            cwd=project_path,
        )
        lines = [line for line in status_result.stdout.strip().split("\n") if line]

        if not lines:
            return f"{branch} [green]✓[/]"

        # Count untracked (??) vs modified
        untracked = sum(1 for line in lines if line.startswith("??"))

        if untracked > 0:
            # Red: has untracked files
            return f"{branch} [red]●[/] {len(lines)} changed"
        else:
            # Yellow: modified only
            return f"{branch} [yellow]●[/] {len(lines)} changed"
    except Exception:
        return ""
