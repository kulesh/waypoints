"""TUI screen for viewing genspec files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer

from waypoints.tui.widgets.genspec_browser import GenSpecBrowser
from waypoints.tui.widgets.header import StatusHeader

logger = logging.getLogger(__name__)


class GenSpecViewerScreen(Screen[None]):
    """View a genspec JSONL or bundle file in the TUI."""

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("escape", "back", "Back", show=True),
    ]

    def __init__(self, path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._path = path

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        yield GenSpecBrowser(
            legend_items=["[Enter] Detail"],
            show_source_info=True,
            id="genspec-browser",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Load the genspec file and populate the browser."""
        self.app.sub_title = f"GenSpec Viewer · {self._path.name}"
        self._load_spec()

    def _load_spec(self) -> None:
        from waypoints.genspec.viewer import load_genspec

        try:
            spec, metadata, checksums = load_genspec(self._path)
        except Exception as e:
            logger.exception("Failed to load genspec: %s", e)
            self.notify(f"Failed to load: {e}", severity="error")
            return

        source_label = "bundle" if metadata else "jsonl"
        browser = self.query_one("#genspec-browser", GenSpecBrowser)
        browser.set_spec(
            spec,
            source_label=source_label,
            metadata=metadata,
            checksums=checksums,
            select_first=False,
        )
        browser.focus_tree()

    def action_back(self) -> None:
        self.app.pop_screen()
