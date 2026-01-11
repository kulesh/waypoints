"""Main Waypoints TUI application."""

import logging
from typing import Any, cast

from textual.app import App
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider

from waypoints.config import settings
from waypoints.git import GitConfig, GitService
from waypoints.llm.metrics import MetricsCollector
from waypoints.models import PHASE_TO_STATE, Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader
from waypoints.tui.screens.chart import ChartScreen
from waypoints.tui.screens.fly import FlyScreen
from waypoints.tui.screens.idea_brief import IdeaBriefScreen
from waypoints.tui.screens.ideation import IdeationScreen
from waypoints.tui.screens.ideation_qa import IdeationQAScreen
from waypoints.tui.screens.land import LandScreen
from waypoints.tui.screens.product_spec import ProductSpecScreen
from waypoints.tui.widgets.header import StatusHeader

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


class WaypointsCommands(Provider):
    """Command provider for Waypoints-specific commands."""

    async def discover(self) -> Hits:
        """Return default commands shown before user input."""
        yield DiscoveryHit(
            "Settings",
            self._open_settings,
            help="Open application settings",
        )

    async def search(self, query: str) -> Hits:
        """Search for Waypoints commands."""
        matcher = self.matcher(query)
        command = "Settings"

        # Filter by match score
        match = matcher.match(command)
        if match > 0:
            yield Hit(
                match,
                matcher.highlight(command),
                self._open_settings,
                help="Open application settings",
            )

    async def _open_settings(self) -> None:
        """Open the settings modal."""
        from waypoints.tui.screens.settings import SettingsModal

        self.app.push_screen(SettingsModal())


class WaypointsApp(App[None]):
    """Main Waypoints TUI application."""

    TITLE = "Waypoints"
    SUB_TITLE = "AI-native software development"

    COMMANDS = App.COMMANDS | {WaypointsCommands}

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+d", "toggle_dark", "Toggle Dark Mode"),
    ]

    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Initialize metrics collector
        # Will be updated when a project is selected
        self._metrics_collector: MetricsCollector | None = None
        self._current_project_slug: str | None = None

    @property
    def metrics_collector(self) -> MetricsCollector | None:
        """Get the metrics collector for the current project."""
        return self._metrics_collector

    def set_project_for_metrics(self, project: Project) -> None:
        """Set the project for metrics collection.

        Creates or updates the MetricsCollector for the project.
        """
        if project.slug != self._current_project_slug:
            self._current_project_slug = project.slug
            self._metrics_collector = MetricsCollector(project)
            logger.info("Metrics collector initialized for project: %s", project.slug)

    def update_header_cost(self) -> None:
        """Update the header cost display from the metrics collector."""
        if self._metrics_collector is None:
            return

        try:
            header = self.screen.query_one(StatusHeader)
            header.update_cost(self._metrics_collector.total_cost)
        except Exception:
            # Header might not exist on all screens
            pass

    def on_mount(self) -> None:
        """Start app showing project selection screen."""
        # Load saved theme
        saved_theme = settings.theme
        logger.info("Loading saved theme: %s", saved_theme)
        self.theme = saved_theme

        # Always show project selection screen first
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.push_screen(ProjectSelectionScreen())

    def _resume_project(self, project: Project) -> None:
        """Resume a project from its current journey state."""
        if project.journey is None:
            logger.warning("Project %s has no journey, starting fresh", project.slug)
            self.push_screen(IdeationScreen())
            return

        # Recover to a safe state if needed
        journey = project.journey.recover()
        if journey != project.journey:
            logger.info(
                "Recovered journey from %s to %s",
                project.journey.state.value,
                journey.state.value,
            )
            project.journey = journey
            project.save()

        phase = journey.phase
        logger.info("Resuming project %s at phase: %s", project.slug, phase)

        # Load necessary data based on phase
        if phase == "ideation":
            self.push_screen(IdeationScreen())
        elif phase == "ideation-qa":
            # Resume Q&A - need the initial idea
            self.push_screen(
                IdeationQAScreen(project=project, idea=project.initial_idea)
            )
        elif phase == "idea-brief":
            # Resume brief review - load existing brief
            brief = self._load_latest_doc(project, "idea-brief")
            if brief:
                self._resume_brief_review(project, brief)
            else:
                # No brief found, go back to ideation
                logger.warning("No idea-brief found, starting fresh")
                self.push_screen(IdeationScreen())
        elif phase == "product-spec":
            # Resume spec review - load existing spec and brief
            spec = self._load_latest_doc(project, "product-spec")
            brief = self._load_latest_doc(project, "idea-brief")
            if spec:
                self._resume_spec_review(project, spec, brief)
            elif brief:
                # No spec but have brief - go to spec generation
                self.push_screen(ProductSpecScreen(project=project, brief=brief))
            else:
                logger.warning("No product-spec or brief found, starting fresh")
                self.push_screen(IdeationScreen())
        elif phase == "chart":
            # Resume chart review - load spec
            spec = self._load_latest_doc(project, "product-spec")
            brief = self._load_latest_doc(project, "idea-brief")
            if spec:
                self._resume_chart_review(project, spec, brief)
            else:
                logger.warning("No product-spec found, starting fresh")
                self.push_screen(IdeationScreen())
        elif phase == "fly":
            # Resume fly phase - load flight plan and spec
            flight_plan = FlightPlanReader.load(project)
            spec = self._load_latest_doc(project, "product-spec")
            if flight_plan and spec:
                self.push_screen(
                    FlyScreen(project=project, flight_plan=flight_plan, spec=spec)
                )
            else:
                logger.warning("No flight-plan or spec found, starting fresh")
                self.push_screen(IdeationScreen())
        else:
            logger.warning("Unknown phase %s, starting fresh", phase)
            self.push_screen(IdeationScreen())

    def _load_latest_doc(self, project: Project, doc_type: str) -> str | None:
        """Load the latest document of a given type from project docs.

        Args:
            project: The project to load from.
            doc_type: Document type prefix (e.g., "idea-brief", "product-spec").

        Returns:
            Document content as string, or None if not found.
        """
        docs_path = project.get_docs_path()
        if not docs_path.exists():
            return None

        # Find all matching files and get the latest by name (timestamp in filename)
        pattern = f"{doc_type}-*.md"
        matching_files = sorted(docs_path.glob(pattern), reverse=True)

        if not matching_files:
            return None

        latest_file = matching_files[0]
        logger.info("Loading %s from %s", doc_type, latest_file.name)
        return latest_file.read_text()

    def _resume_brief_review(self, project: Project, brief: str) -> None:
        """Resume at brief review with existing content."""
        from waypoints.tui.screens.idea_brief import IdeaBriefResumeScreen

        self.push_screen(IdeaBriefResumeScreen(project=project, brief=brief))

    def _resume_spec_review(
        self, project: Project, spec: str, brief: str | None
    ) -> None:
        """Resume at spec review with existing content."""
        from waypoints.tui.screens.product_spec import ProductSpecResumeScreen

        self.push_screen(
            ProductSpecResumeScreen(project=project, spec=spec, brief=brief)
        )

    def _resume_chart_review(
        self, project: Project, spec: str, brief: str | None
    ) -> None:
        """Resume at chart review - load existing flight plan if any."""
        flight_plan = FlightPlanReader.load(project)
        if flight_plan:
            # Have a flight plan, go directly to fly
            self.push_screen(
                FlyScreen(project=project, flight_plan=flight_plan, spec=spec)
            )
        else:
            # No flight plan yet, go to chart screen
            self.push_screen(ChartScreen(project=project, spec=spec, brief=brief))

    def watch_theme(self, new_theme: str) -> None:
        """Save theme whenever it changes (from any source)."""
        logger.info("Theme changed to: %s, saving...", new_theme)
        settings.theme = new_theme

    def switch_phase(self, phase: str, data: dict[str, Any] | None = None) -> None:
        """Switch to a different phase, optionally with data."""
        data = data or {}
        project: Project | None = cast(Project | None, data.get("project"))
        from_phase = data.get("from_phase")

        # Log journey state for debugging
        if project:
            target_state = PHASE_TO_STATE.get(phase)
            current_state = project.journey.state if project.journey else None
            logger.info(
                "Phase switch: %s -> %s (journey: %s -> %s)",
                from_phase,
                phase,
                current_state.value if current_state else "none",
                target_state.value if target_state else "unknown",
            )

        # Commit phase transition if we have a project and a from_phase
        if project and from_phase:
            self._commit_phase_transition(project, from_phase, phase)

        if phase == "ideation":
            self.switch_screen(IdeationScreen())
        elif phase == "ideation-qa" and project:
            self.switch_screen(
                IdeationQAScreen(
                    project=project,
                    idea=data.get("idea", ""),
                )
            )
        elif phase == "idea-brief" and project:
            history = cast(DialogueHistory, data.get("history"))
            self.switch_screen(
                IdeaBriefScreen(
                    project=project,
                    idea=data.get("idea", ""),
                    history=history,
                )
            )
        elif phase == "product-spec" and project:
            self.switch_screen(
                ProductSpecScreen(
                    project=project,
                    idea=data.get("idea"),
                    brief=data.get("brief"),
                    history=data.get("history"),
                )
            )
        elif phase == "chart" and project:
            self.switch_screen(
                ChartScreen(
                    project=project,
                    spec=data.get("spec", ""),
                    idea=data.get("idea"),
                    brief=data.get("brief"),
                    history=data.get("history"),
                )
            )
        elif phase == "fly" and project:
            flight_plan = cast(FlightPlan, data.get("flight_plan"))
            self.switch_screen(
                FlyScreen(
                    project=project,
                    flight_plan=flight_plan,
                    spec=data.get("spec", ""),
                )
            )
        elif phase == "land" and project:
            land_flight_plan = cast(FlightPlan | None, data.get("flight_plan"))
            self.switch_screen(
                LandScreen(
                    project=project,
                    flight_plan=land_flight_plan,
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
        project_path = project.get_path()
        config = GitConfig.load(project.slug)

        if not config.auto_commit:
            logger.debug("Auto-commit disabled, skipping phase commit")
            return

        # Get commit config for this phase
        phase_config = PHASE_COMMITS.get(to_phase)
        if not phase_config:
            logger.debug("No commit config for phase: %s", to_phase)
            return

        git = GitService(project_path)

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
