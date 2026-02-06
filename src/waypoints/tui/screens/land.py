"""Land screen for project completion (LAND phase).

Hub screen with four activities:
- Debrief: Completion stats, issues, lessons learned
- Ship: Changelog, release notes, git tagging
- Iterate: Next steps, V2 planning, project close
- Gen Spec: View generative spec details and export to file
"""

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.models import JourneyState, Project
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader
from waypoints.models.waypoint import WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.widgets.genspec_browser import GenSpecBrowser
from waypoints.tui.widgets.header import StatusHeader

logger = logging.getLogger(__name__)


class LandActivity(Enum):
    """Activities available on the Land screen."""

    DEBRIEF = "debrief"
    SHIP = "ship"
    ITERATE = "iterate"
    GENSPEC = "genspec"


class ActivityListPanel(Vertical):
    """Left panel showing list of activities."""

    DEFAULT_CSS = """
    ActivityListPanel {
        width: 20;
        height: 100%;
        border-right: solid $surface-lighten-1;
    }

    ActivityListPanel .panel-title {
        text-style: bold;
        color: $text;
        padding: 1 0 0 0;
        text-align: center;
        border-bottom: solid $surface-lighten-1;
    }

    ActivityListPanel OptionList {
        height: 1fr;
        background: transparent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("ACTIVITIES", classes="panel-title")
        yield OptionList(
            Option("Debrief", id="debrief"),
            Option("Ship", id="ship"),
            Option("Iterate", id="iterate"),
            Option("Gen Spec", id="genspec"),
            id="activity-list",
        )


class DebriefPanel(VerticalScroll):
    """Debrief content panel - shows completion stats, issues, and project context."""

    DEFAULT_CSS = """
    DebriefPanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    DebriefPanel .section-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
        margin-bottom: 0;
    }

    DebriefPanel .stat-line {
        color: $text-muted;
        padding-left: 2;
    }

    DebriefPanel .issue-line {
        color: $warning;
        padding-left: 2;
    }

    DebriefPanel .success-line {
        color: $success;
        padding-left: 2;
    }

    DebriefPanel .failed-line {
        color: $error;
        padding-left: 2;
    }

    DebriefPanel .muted-line {
        color: $text-muted;
        padding-left: 2;
    }

    DebriefPanel .summary-paragraph {
        color: $text-muted;
        padding: 0 0 1 0;
    }
    """

    def __init__(self, project: Project, flight_plan: FlightPlan | None, **kwargs: Any):
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan

    def compose(self) -> ComposeResult:
        yield Static("DEBRIEF", classes="section-title")
        yield Static("", id="summary-content", classes="summary-paragraph")
        yield Static("", id="stats-content")
        yield Static("Project Outputs", classes="section-title")
        yield Static("", id="outputs-content")
        yield Static("Execution Details", classes="section-title")
        yield Static("", id="execution-content")
        yield Static("Git Context", classes="section-title")
        yield Static("", id="git-content")
        yield Static("Top Spenders", classes="section-title")
        yield Static("", id="waypoint-costs-content")
        yield Static("Outstanding Issues", classes="section-title")
        yield Static("", id="issues-content")
        yield Static("Quality Gates", classes="section-title")
        yield Static("", id="quality-content")

    def on_mount(self) -> None:
        """Load and display debrief data via DebriefService."""
        from waypoints.orchestration.debrief import DebriefService

        data = DebriefService(self.project, self.flight_plan).generate()

        self.query_one("#summary-content", Static).update(data.summary)
        self.query_one("#stats-content", Static).update("\n".join(data.stats))

        outputs_widget = self.query_one("#outputs-content", Static)
        outputs_widget.update("\n".join(data.outputs))
        outputs_widget.add_class("muted-line")

        execution_widget = self.query_one("#execution-content", Static)
        execution_widget.update("\n".join(data.execution))
        execution_widget.add_class("muted-line")

        git_widget = self.query_one("#git-content", Static)
        git_widget.update("\n".join(data.git_context))
        git_widget.add_class("muted-line")

        costs_widget = self.query_one("#waypoint-costs-content", Static)
        costs_widget.update("\n".join(data.waypoint_costs))
        costs_widget.add_class("muted-line")

        issues_widget = self.query_one("#issues-content", Static)
        if data.has_issues:
            issues_widget.update("\n".join(data.issues))
            issues_widget.add_class("issue-line")
        else:
            issues_widget.update("\n".join(data.issues))
            issues_widget.add_class("success-line")

        quality_widget = self.query_one("#quality-content", Static)
        quality_widget.update("\n".join(data.quality_gates))
        quality_widget.add_class("muted-line")


class ShipPanel(VerticalScroll):
    """Ship content panel - changelog, release notes, versioning."""

    DEFAULT_CSS = """
    ShipPanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    ShipPanel .section-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
        margin-bottom: 0;
    }

    ShipPanel .content {
        color: $text-muted;
        padding-left: 2;
    }

    ShipPanel .hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 2;
    }
    """

    def __init__(self, project: Project, flight_plan: FlightPlan | None, **kwargs: Any):
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan

    def compose(self) -> ComposeResult:
        yield Static("SHIP", classes="section-title")
        yield Markdown("", id="changelog-content", classes="content")
        yield Static("", classes="hint", id="ship-hint")

    def on_mount(self) -> None:
        """Show release notes or changelog preview."""
        self._update_changelog()

    def _update_changelog(self) -> None:
        """Show release notes if available, otherwise show changelog preview."""
        content = self.query_one("#changelog-content", Markdown)

        # Check for generated release notes
        release_notes_path = self.project.get_docs_path() / "release-notes.md"
        if release_notes_path.exists():
            notes = release_notes_path.read_text()
            content.update(notes)
            return

        # Fallback to basic changelog preview
        lines: list[str] = ["Changelog Preview:", ""]

        if self.flight_plan:
            completed = [
                wp
                for wp in self.flight_plan.waypoints
                if wp.status == WaypointStatus.COMPLETE and not wp.parent_id
            ]
            for wp in completed:
                lines.append(f"- {wp.title}")

        content.update("\n".join(lines) if lines else "No completed waypoints")


class IteratePanel(VerticalScroll):
    """Iterate content panel - next steps and project closure."""

    DEFAULT_CSS = """
    IteratePanel {
        width: 1fr;
        height: 100%;
        padding: 1 2;
    }

    IteratePanel .section-title {
        text-style: bold;
        color: $text;
        margin-top: 1;
        margin-bottom: 0;
    }

    IteratePanel .content {
        color: $text-muted;
        padding-left: 2;
    }

    IteratePanel .hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 2;
    }
    """

    def __init__(self, project: Project, **kwargs: Any):
        super().__init__(**kwargs)
        self.project = project

    def compose(self) -> ComposeResult:
        yield Static("ITERATE", classes="section-title")
        yield Static("", id="iterate-content", classes="content")
        yield Static("", classes="hint", id="iterate-hint")

    def on_mount(self) -> None:
        """Show iteration options."""
        content = self.query_one("#iterate-content", Static)
        content.update(
            "What's next?\n\n"
            "├─ Start V2 iteration (new features)\n"
            "├─ Mark project as closed\n"
            "└─ Return to project list"
        )


class GenSpecPanel(Vertical):
    """Gen Spec panel with browseable tree and preview."""

    def __init__(self, project: Project, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.project = project
        self._spec: Any = None

    def compose(self) -> ComposeResult:
        yield GenSpecBrowser(
            legend_items=["[e] Export", "[Enter] Detail"],
            id="genspec-browser",
        )

    def on_mount(self) -> None:
        """Load the spec and populate the browser."""
        from waypoints.genspec import export_project

        try:
            self._spec = export_project(self.project)
            browser = self.query_one("#genspec-browser", GenSpecBrowser)
            browser.set_spec(self._spec, select_first=True)
            browser.focus_tree()
        except Exception as e:
            logger.exception("Failed to load genspec: %s", e)
            self.app.notify(f"Error loading spec: {e}", severity="error")

    def export_spec(self) -> None:
        """Export the generative spec to a file via modal."""
        from pathlib import Path

        from waypoints.genspec import export_bundle
        from waypoints.tui.widgets.genspec import ExportModal

        if not self._spec:
            self.app.notify("No spec to export", severity="warning")
            return

        def handle_export(result: tuple[str, str] | None) -> None:
            if result is None:
                return  # User cancelled
            directory, filename = result
            output_path = Path(directory) / filename

            try:
                self.app.notify("Exporting...")
                export_bundle(self._spec, output_path)
                self.app.notify(f"Exported bundle to {output_path}")
                logger.info("Exported genspec bundle to %s", output_path)
            except Exception as e:
                self.app.notify(f"Export failed: {e}", severity="error")
                logger.exception("Failed to export genspec: %s", e)

        self.app.push_screen(ExportModal(self.project.slug), handle_export)


class LandScreen(Screen[None]):
    """
    Land screen - Project completion hub.

    Four activities accessible via left panel:
    - Debrief: Stats, issues, lessons
    - Ship: Changelog, release notes, git tag
    - Iterate: V2 planning, close project
    - Gen Spec: View generative spec details and export
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("d", "show_debrief", "Debrief", show=True),
        Binding("s", "show_ship", "Ship", show=True),
        Binding("i", "show_iterate", "Iterate", show=True),
        Binding("v", "show_genspec", "Gen Spec", show=True),
        Binding("f", "fix_issues", "Fix Issues", show=True),
        Binding("n", "new_iteration", "New V2", show=True),
        Binding("c", "close_project", "Close", show=True),
        Binding("g", "generate_release", "Generate", show=False),
        Binding("t", "create_tag", "Tag", show=False),
        Binding("e", "export_genspec", "Export", show=False),
        Binding("h", "toggle_host_validations", "HostVal", show=True),
        Binding("r", "regenerate", "Regenerate", show=True),
    ]

    DEFAULT_CSS = """
    LandScreen {
        background: $surface;
        overflow: hidden;
    }

    LandScreen .main-container {
        width: 100%;
        height: 1fr;
    }

    LandScreen .content-area {
        width: 1fr;
        height: 100%;
    }
    """

    def __init__(
        self,
        project: Project,
        flight_plan: FlightPlan | None = None,
        spec: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan or FlightPlanReader.load(project)
        self.spec = spec
        self._coordinator: JourneyCoordinator | None = None
        self.current_activity: LandActivity = LandActivity.DEBRIEF

    @property
    def coordinator(self) -> JourneyCoordinator:
        """Get the coordinator, creating if needed."""
        if self._coordinator is None:
            self._coordinator = JourneyCoordinator(
                project=self.project,
                flight_plan=self.flight_plan,
            )
        return self._coordinator
        self.current_activity = LandActivity.DEBRIEF

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        with Horizontal(classes="main-container"):
            yield ActivityListPanel(id="activity-panel")
            with Vertical(classes="content-area", id="content-area"):
                yield DebriefPanel(self.project, self.flight_plan, id="debrief-panel")
                yield ShipPanel(self.project, self.flight_plan, id="ship-panel")
                yield IteratePanel(self.project, id="iterate-panel")
                yield GenSpecPanel(self.project, id="genspec-panel")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the Land screen."""
        self.app.sub_title = f"{self.project.name} · Land"

        # Set up metrics collection
        self.waypoints_app.set_project_for_metrics(self.project)
        # Load persisted host validation preference for this project
        self.waypoints_app.host_validations_enabled = (
            self.waypoints_app.load_host_validation_preference(self.project)
        )

        # Show only debrief panel initially
        self._show_activity(LandActivity.DEBRIEF)

        # Focus the activity list
        activity_list = self.query_one("#activity-list", OptionList)
        activity_list.focus()

        logger.info("Land screen mounted for project: %s", self.project.slug)

    def _show_activity(self, activity: LandActivity) -> None:
        """Show the specified activity panel, hide others."""
        self.current_activity = activity

        debrief = self.query_one("#debrief-panel", DebriefPanel)
        ship = self.query_one("#ship-panel", ShipPanel)
        iterate = self.query_one("#iterate-panel", IteratePanel)
        genspec = self.query_one("#genspec-panel", GenSpecPanel)

        debrief.display = activity == LandActivity.DEBRIEF
        ship.display = activity == LandActivity.SHIP
        iterate.display = activity == LandActivity.ITERATE
        genspec.display = activity == LandActivity.GENSPEC

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle activity selection from the list."""
        if event.option.id == "debrief":
            self._show_activity(LandActivity.DEBRIEF)
        elif event.option.id == "ship":
            self._show_activity(LandActivity.SHIP)
        elif event.option.id == "iterate":
            self._show_activity(LandActivity.ITERATE)
        elif event.option.id == "genspec":
            self._show_activity(LandActivity.GENSPEC)

    def action_show_debrief(self) -> None:
        """Show the Debrief panel."""
        self._show_activity(LandActivity.DEBRIEF)
        self._select_activity_option("debrief")

    def action_show_ship(self) -> None:
        """Show the Ship panel."""
        self._show_activity(LandActivity.SHIP)
        self._select_activity_option("ship")

    def action_show_iterate(self) -> None:
        """Show the Iterate panel."""
        self._show_activity(LandActivity.ITERATE)
        self._select_activity_option("iterate")

    def action_show_genspec(self) -> None:
        """Show the Gen Spec panel."""
        self._show_activity(LandActivity.GENSPEC)
        self._select_activity_option("genspec")

    def action_toggle_host_validations(self) -> None:
        """Toggle host validations (used in Fly phase) from Land."""
        app = self.waypoints_app
        app.host_validations_enabled = not app.host_validations_enabled
        state = "ON" if app.host_validations_enabled else "OFF (LLM-as-judge only)"
        app.save_host_validation_preference(self.project)
        self.notify(f"Host validations {state}")
        self.app.bell()
        logger.info("Host validations toggled to %s from Land", state)

    def _select_activity_option(self, option_id: str) -> None:
        """Select the specified option in the activity list."""
        activity_list = self.query_one("#activity-list", OptionList)
        for i, option in enumerate(activity_list._options):
            if option.id == option_id:
                activity_list.highlighted = i
                break

    def action_back(self) -> None:
        """Go back to Fly screen (view only)."""
        self.waypoints_app.switch_phase(
            "fly",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
            },
        )

    def action_fix_issues(self) -> None:
        """Return to Fly screen to fix issues."""
        # Transition back to FLY_READY
        self.coordinator.transition(
            JourneyState.FLY_READY,
            reason="land.fix_issues",
        )
        self.waypoints_app.switch_phase(
            "fly",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
            },
        )

    def action_new_iteration(self) -> None:
        """Start a new V2 iteration."""
        # Transition to SPARK_IDLE for new ideation
        self.coordinator.transition(
            JourneyState.SPARK_IDLE,
            reason="land.start_v2",
        )
        self.notify("Starting V2 iteration...")
        from waypoints.tui.screens.ideation import IdeationScreen

        # Start fresh ideation (new project will be created)
        self.app.switch_screen(IdeationScreen())

    def action_close_project(self) -> None:
        """Mark project as closed."""
        # TODO: Add status field to Project model
        self.notify(f"Project '{self.project.name}' marked as closed")
        from waypoints.tui.screens.project_selection import ProjectSelectionScreen

        self.app.switch_screen(ProjectSelectionScreen())

    def action_generate_release(self) -> None:
        """Regenerate release notes."""
        if self.current_activity == LandActivity.SHIP:
            self.project._generate_release_notes()
            # Refresh the ship panel
            ship_panel = self.query_one("#ship-panel", ShipPanel)
            ship_panel._update_changelog()
            self.notify("Release notes regenerated")

    def action_create_tag(self) -> None:
        """Create git tag (placeholder)."""
        if self.current_activity == LandActivity.SHIP:
            self.notify("Git tagging not yet implemented")

    def action_export_genspec(self) -> None:
        """Export the generative spec when in Gen Spec panel."""
        if self.current_activity == LandActivity.GENSPEC:
            genspec_panel = self.query_one("#genspec-panel", GenSpecPanel)
            genspec_panel.export_spec()

    def action_regenerate(self) -> None:
        """Start regeneration from the generative specification."""
        from waypoints.tui.widgets.genspec import RegenerateModal

        self.app.push_screen(RegenerateModal(self.project))
