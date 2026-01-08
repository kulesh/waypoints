"""Main Waypoints TUI application."""

import logging
from typing import Any

from textual.app import App
from textual.binding import Binding

from waypoints.config import settings
from waypoints.tui.screens.idea_brief import IdeaBriefScreen
from waypoints.tui.screens.ideation import IdeationScreen
from waypoints.tui.screens.ideation_qa import IdeationQAScreen
from waypoints.tui.screens.product_spec import ProductSpecScreen

logger = logging.getLogger(__name__)


class WaypointsApp(App):
    """Main Waypoints TUI application."""

    TITLE = "Waypoints"
    SUB_TITLE = "AI-native software development"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+d", "toggle_dark", "Toggle Dark Mode"),
    ]

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def on_mount(self) -> None:
        """Start with SPARK phase and load saved settings."""
        # Load saved theme
        saved_theme = settings.theme
        logger.info("Loading saved theme: %s", saved_theme)
        self.theme = saved_theme
        self.push_screen(IdeationScreen())

    def watch_theme(self, new_theme: str) -> None:
        """Save theme whenever it changes (from any source)."""
        logger.info("Theme changed to: %s, saving...", new_theme)
        settings.theme = new_theme

    def switch_phase(self, phase: str, data: dict[str, Any] | None = None) -> None:
        """Switch to a different phase, optionally with data."""
        data = data or {}

        if phase == "ideation":
            self.switch_screen(IdeationScreen())
        elif phase == "ideation-qa":
            self.switch_screen(IdeationQAScreen(idea=data.get("idea", "")))
        elif phase == "idea-brief":
            self.switch_screen(
                IdeaBriefScreen(
                    idea=data.get("idea", ""),
                    history=data.get("history"),
                )
            )
        elif phase == "product-spec":
            self.switch_screen(
                ProductSpecScreen(
                    idea=data.get("idea"),
                    brief=data.get("brief"),
                    history=data.get("history"),
                )
            )
        # Future: add Waypoints screen

    def action_toggle_dark(self) -> None:
        """Toggle dark mode (saving handled by watch_theme)."""
        self.theme = (
            "textual-dark" if self.theme == "textual-light" else "textual-light"
        )
