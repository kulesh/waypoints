"""TUI utility functions for Waypoints."""

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from textual.app import App

# TUI editors that need app suspension
TUI_EDITORS = {"vim", "nvim", "vi", "emacs", "nano", "micro", "helix", "ne", "joe"}


def get_editor() -> str:
    """Get user's preferred editor from environment."""
    return os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vim"


def is_tui_editor(editor: str) -> bool:
    """Check if editor requires terminal (vs GUI)."""
    return Path(editor).stem in TUI_EDITORS


def edit_file_in_editor(
    app: "App[Any]", file_path: Path, on_complete: Callable[[], None] | None = None
) -> None:
    """Open file in user's $EDITOR, handling TUI vs GUI editors.

    Args:
        app: The Textual app (for suspend/resume)
        file_path: Path to the file to edit
        on_complete: Optional callback after editing completes
    """
    editor = get_editor()
    args = [editor, str(file_path)]

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
