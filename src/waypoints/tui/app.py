"""Main Waypoints TUI application."""

import logging
from typing import Any, cast

from textual.app import App
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider

from waypoints.config import settings
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
        # Track theme before toggling so we can restore it
        self._previous_theme: str | None = None

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
        elif phase == "land":
            # Resume land phase - load flight plan and spec
            flight_plan = FlightPlanReader.load(project)
            spec = self._load_latest_doc(project, "product-spec")
            self.push_screen(
                LandScreen(project=project, flight_plan=flight_plan, spec=spec or "")
            )
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
        """Resume at chart review - show chart screen to review/edit plan."""
        # Always show ChartScreen when in CHART_REVIEW state.
        # Even if a flight plan exists, the user should review it before flying.
        # ChartScreen will load and display any existing flight plan.
        self.push_screen(ChartScreen(project=project, spec=spec, brief=brief))

    def watch_theme(self, new_theme: str) -> None:
        """Save theme whenever it changes (from any source)."""
        logger.info("Theme changed to: %s, saving...", new_theme)
        settings.theme = new_theme

    def switch_phase(self, phase: str, data: dict[str, Any] | None = None) -> None:
        """Switch to a different phase, optionally with data.

        Note: Git commits happen in Project.transition_journey(), not here.
        This method only handles screen navigation.
        """
        data = data or {}
        project: Project | None = cast(Project | None, data.get("project"))

        # Log journey state for debugging
        if project:
            target_state = PHASE_TO_STATE.get(phase)
            current_state = project.journey.state if project.journey else None
            logger.info(
                "Screen switch to %s (journey: %s -> %s)",
                phase,
                current_state.value if current_state else "none",
                target_state.value if target_state else "unknown",
            )

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
        """Toggle dark mode (saving handled by watch_theme).

        If toggling back, restores the previous theme instead of defaulting
        to textual-dark/textual-light.
        """
        if self._previous_theme is not None:
            # Restore previous theme
            restored = self._previous_theme
            self._previous_theme = None
            self.theme = restored
        else:
            # Store current theme and switch to opposite
            self._previous_theme = self.theme
            self.theme = (
                "textual-dark" if self.theme == "textual-light" else "textual-light"
            )
