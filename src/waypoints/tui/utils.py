"""TUI utility functions for Waypoints."""

import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from textual.app import App

from waypoints.models.waypoint import WaypointStatus

logger = logging.getLogger(__name__)


# =============================================================================
# Time Formatting Utilities
# =============================================================================


def format_duration(seconds: int, suffix: str = "") -> str:
    """Format duration in human-readable form.

    Args:
        seconds: Duration in seconds
        suffix: Optional suffix to append (e.g., " total")

    Returns:
        Formatted string like "1h 23m", "5m 30s", or "45s"
    """
    mins, secs = divmod(seconds, 60)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f"{hours}h {mins}m{suffix}"
    if mins > 0:
        return f"{mins}m {secs}s{suffix}"
    return f"{secs}s{suffix}"


def format_relative_time(dt: datetime) -> str:
    """Format datetime as relative time string.

    Args:
        dt: The datetime to format (naive datetimes are assumed to be UTC)

    Returns:
        Relative time string like "5m ago", "2h ago", "3d ago"
    """
    now = datetime.now(UTC)
    # Handle naive datetimes (assumed to be UTC) for backwards compatibility
    if dt.tzinfo is None:
        now = now.replace(tzinfo=None)
    total_seconds = (now - dt).total_seconds()

    thresholds = [
        (365 * 24 * 3600, 365 * 24 * 3600, "y ago"),
        (30 * 24 * 3600, 30 * 24 * 3600, "mo ago"),
        (24 * 3600, 24 * 3600, "d ago"),
        (3600, 3600, "h ago"),
        (60, 60, "m ago"),
    ]

    for threshold, divisor, suffix in thresholds:
        if total_seconds >= threshold:
            return f"{int(total_seconds // divisor)}{suffix}"
    return "just now"


# =============================================================================
# Waypoint Status Display
# =============================================================================

# Centralized status display configuration: (icon, color, label)
WAYPOINT_STATUS_DISPLAY: dict[WaypointStatus, tuple[str, str, str]] = {
    WaypointStatus.COMPLETE: ("✓", "green", "Complete"),
    WaypointStatus.FAILED: ("✗", "red", "Failed"),
    WaypointStatus.IN_PROGRESS: ("●", "yellow", "Running"),
    WaypointStatus.PENDING: ("○", "dim", "Pending"),
    WaypointStatus.SKIPPED: ("⊘", "dim", "Skipped"),
}


def get_status_icon(status: WaypointStatus) -> str:
    """Get the icon character for a waypoint status."""
    icon, _, _ = WAYPOINT_STATUS_DISPLAY.get(status, ("?", "dim", "Unknown"))
    return icon


def get_status_color(status: WaypointStatus) -> str:
    """Get the color name for a waypoint status."""
    _, color, _ = WAYPOINT_STATUS_DISPLAY.get(status, ("?", "dim", "Unknown"))
    return color


def get_status_label(status: WaypointStatus) -> str:
    """Get the human-readable label for a waypoint status."""
    _, _, label = WAYPOINT_STATUS_DISPLAY.get(status, ("?", "dim", "Unknown"))
    return label


def get_status_markup(status: WaypointStatus) -> str:
    """Get Rich markup for a waypoint status (icon with color).

    Returns:
        String like "[green]✓[/]" for use in Rich text.
    """
    icon, color, _ = WAYPOINT_STATUS_DISPLAY.get(status, ("?", "dim", "Unknown"))
    return f"[{color}]{icon}[/]"


# TUI editors that need app suspension
TUI_EDITORS = {"vim", "nvim", "vi", "emacs", "nano", "micro", "helix", "ne", "joe"}

# Allowlist of known safe editors (by executable name stem)
SAFE_EDITORS = {
    # TUI editors
    "vim",
    "nvim",
    "vi",
    "emacs",
    "nano",
    "micro",
    "helix",
    "ne",
    "joe",
    "pico",
    "ed",
    # GUI editors
    "code",
    "subl",
    "atom",
    "gedit",
    "kate",
    "notepad",
    "notepad++",
    "textmate",
    "mate",
    "bbedit",
    "brackets",
    "zed",
    "cursor",
}


def get_editor() -> str:
    """Get user's preferred editor from environment."""
    return os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vim"


def is_tui_editor(editor: str) -> bool:
    """Check if editor requires terminal (vs GUI)."""
    return Path(editor).stem in TUI_EDITORS


def validate_editor(editor: str) -> str | None:
    """Validate that an editor is safe to execute.

    Returns the resolved path to the editor if valid, None otherwise.

    Validation rules:
    1. Editor name (stem) must be in SAFE_EDITORS allowlist
    2. Editor must be resolvable via shutil.which() or be an absolute path
    3. No shell metacharacters allowed
    """
    # Reject shell metacharacters that could enable injection
    dangerous_chars = {";", "&", "|", "$", "`", "(", ")", "{", "}", "<", ">", "\n"}
    if any(c in editor for c in dangerous_chars):
        logger.warning("Editor contains dangerous characters: %s", editor)
        return None

    # Get the editor name (stem) for allowlist check
    editor_stem = Path(editor).stem.lower()

    # Check if editor is in allowlist
    if editor_stem not in SAFE_EDITORS:
        logger.warning("Editor not in allowlist: %s", editor)
        return None

    # Resolve the editor path
    if os.path.isabs(editor):
        # Absolute path - verify it exists and is executable
        if os.path.isfile(editor) and os.access(editor, os.X_OK):
            return editor
        logger.warning("Absolute editor path not executable: %s", editor)
        return None

    # Relative name - use which to find it
    resolved = shutil.which(editor)
    if resolved:
        return resolved

    logger.warning("Editor not found in PATH: %s", editor)
    return None


def edit_file_in_editor(
    app: "App[Any]", file_path: Path, on_complete: Callable[[], None] | None = None
) -> bool:
    """Open file in user's $EDITOR, handling TUI vs GUI editors.

    Args:
        app: The Textual app (for suspend/resume)
        file_path: Path to the file to edit
        on_complete: Optional callback after editing completes

    Returns:
        True if editor was launched, False if editor validation failed.
    """
    editor = get_editor()

    # Validate editor before execution
    resolved_editor = validate_editor(editor)
    if resolved_editor is None:
        logger.error(
            "Editor validation failed for '%s'. " "Set $EDITOR to a known editor: %s",
            editor,
            ", ".join(sorted(SAFE_EDITORS)[:10]) + "...",
        )
        return False

    args = [resolved_editor, str(file_path)]

    if is_tui_editor(editor):
        # TUI editors: suspend app, run editor (blocking), resume
        with app.suspend():
            subprocess.run(args)
        if on_complete:
            on_complete()
    else:
        # GUI editors: non-blocking, callback immediately
        subprocess.Popen(args)
        if on_complete:
            on_complete()

    return True
