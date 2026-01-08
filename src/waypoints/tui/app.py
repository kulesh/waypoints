"""Main Waypoints TUI application."""

import logging
from typing import Any

from textual.app import App
from textual.binding import Binding

from waypoints.config import settings
from waypoints.git import GitConfig, GitService
from waypoints.models import Project
from waypoints.tui.screens.chart import ChartScreen
from waypoints.tui.screens.fly import FlyScreen
from waypoints.tui.screens.idea_brief import IdeaBriefScreen
from waypoints.tui.screens.ideation import IdeationScreen
from waypoints.tui.screens.ideation_qa import IdeationQAScreen
from waypoints.tui.screens.product_spec import ProductSpecScreen

logger = logging.getLogger(__name__)

# Phase transition commit messages and tags
PHASE_COMMITS: dict[str, dict[str, str | None]] = {
    "idea-brief": {
        "message": "feat({slug}): Complete ideation phase",
        "tag": "{slug}/idea-brief",
    },
    "product-spec": {
        "message": "feat({slug}): Finalize idea brief",
        "tag": None,
    },
    "chart": {
        "message": "feat({slug}): Complete product specification",
        "tag": "{slug}/spec",
    },
    "fly": {
        "message": "feat({slug}): Flight plan ready for takeoff",
        "tag": "{slug}/ready",
    },
}


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
        project = data.get("project")
        from_phase = data.get("from_phase")

        # Commit phase transition if we have a project and a from_phase
        if project and from_phase:
            self._commit_phase_transition(project, from_phase, phase)

        if phase == "ideation":
            self.switch_screen(IdeationScreen())
        elif phase == "ideation-qa":
            self.switch_screen(
                IdeationQAScreen(
                    project=project,
                    idea=data.get("idea", ""),
                )
            )
        elif phase == "idea-brief":
            self.switch_screen(
                IdeaBriefScreen(
                    project=project,
                    idea=data.get("idea", ""),
                    history=data.get("history"),
                )
            )
        elif phase == "product-spec":
            self.switch_screen(
                ProductSpecScreen(
                    project=project,
                    idea=data.get("idea"),
                    brief=data.get("brief"),
                    history=data.get("history"),
                )
            )
        elif phase == "chart":
            self.switch_screen(
                ChartScreen(
                    project=project,
                    spec=data.get("spec", ""),
                    idea=data.get("idea"),
                    brief=data.get("brief"),
                    history=data.get("history"),
                )
            )
        elif phase == "fly":
            self.switch_screen(
                FlyScreen(
                    project=project,
                    flight_plan=data.get("flight_plan"),
                    spec=data.get("spec", ""),
                )
            )

    def action_toggle_dark(self) -> None:
        """Toggle dark mode (saving handled by watch_theme)."""
        self.theme = (
            "textual-dark" if self.theme == "textual-light" else "textual-light"
        )

    def _commit_phase_transition(
        self, project: Project, from_phase: str, to_phase: str
    ) -> None:
        """Commit project artifacts at phase transition.

        Unlike waypoint completion (which requires receipt validation),
        phase transitions just commit the generated artifacts directly.
        """
        config = GitConfig.load()

        if not config.auto_commit:
            logger.debug("Auto-commit disabled, skipping phase commit")
            return

        # Get commit config for this phase
        phase_config = PHASE_COMMITS.get(to_phase)
        if not phase_config:
            logger.debug("No commit config for phase: %s", to_phase)
            return

        git = GitService()

        # Auto-init if needed
        if not git.is_git_repo():
            if config.auto_init:
                result = git.init_repo()
                if result.success:
                    self.notify("Initialized git repository")
                else:
                    logger.warning("Failed to init git repo: %s", result.message)
                    return
            else:
                logger.debug("Not a git repo and auto-init disabled")
                return

        # Stage project files
        git.stage_project_files(project.slug)

        # Build and execute commit
        message = phase_config["message"]
        if message:
            commit_msg = message.format(slug=project.slug)
            result = git.commit(commit_msg)

            if result.success and "Nothing to commit" not in result.message:
                logger.info("Phase commit: %s", commit_msg)
                self.notify(f"Committed: {to_phase} phase")

                # Create tag if configured
                if config.create_phase_tags:
                    tag_template = phase_config.get("tag")
                    if tag_template:
                        tag_name = tag_template.format(slug=project.slug)
                        tag_result = git.tag(
                            tag_name, f"Phase transition: {from_phase} → {to_phase}"
                        )
                        if tag_result.success:
                            logger.info("Created tag: %s", tag_name)
            elif not result.success:
                logger.error("Phase commit failed: %s", result.message)
