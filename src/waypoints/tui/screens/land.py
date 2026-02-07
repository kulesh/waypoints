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

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.models import JourneyState, Project
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader
from waypoints.models.iteration_request import IterationRequestWriter
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.orchestration.iteration_service import (
    IterationAttachment,
    IterationRequestService,
)
from waypoints.tui.widgets.flight_plan import AddWaypointPreviewModal
from waypoints.tui.widgets.genspec_browser import GenSpecBrowser
from waypoints.tui.widgets.header import StatusHeader

logger = logging.getLogger(__name__)


class LandActivity(Enum):
    """Activities available on the Land screen."""

    DEBRIEF = "debrief"
    SHIP = "ship"
    ITERATE = "iterate"
    GENSPEC = "genspec"


class IterationSubmitRequested(Message):
    """Request to submit an iteration prompt."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class IterationComposerInput(TextArea):
    """Multiline composer with Enter-submit and Shift+Enter newline."""

    async def _on_key(self, event: Key) -> None:
        """Handle submit/newline behavior before TextArea processing."""
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            if text := self.text.strip():
                self.post_message(IterationSubmitRequested(text))
            return

        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return

        await super()._on_key(event)


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
    """Codex-style composer for bug reports, feature requests, and refinements."""

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

    IteratePanel IterationComposerInput {
        width: 100%;
        height: 12;
        border: none;
        background: transparent;
        padding: 0;
        margin-top: 1;
    }

    IteratePanel IterationComposerInput:focus {
        border: none;
    }

    IteratePanel .attachments {
        margin-top: 1;
        color: $text-muted;
    }

    IteratePanel .status {
        margin-top: 1;
        color: $text;
    }

    IteratePanel .hint {
        color: $text-disabled;
        text-style: italic;
        margin-top: 1;
    }
    """

    def __init__(self, project: Project, **kwargs: Any):
        super().__init__(**kwargs)
        self.project = project

    def compose(self) -> ComposeResult:
        yield Static("ITERATE", classes="section-title")
        yield IterationComposerInput(id="iterate-input")
        yield Static(
            "Attachments: none",
            id="iterate-attachments",
            classes="attachments",
        )
        yield Static("", id="iterate-status", classes="status")
        yield Static("", classes="hint", id="iterate-hint")

    def on_mount(self) -> None:
        """Initialize composer copy."""
        hint = self.query_one("#iterate-hint", Static)
        hint.update(
            "Describe what is broken or what to improve. Drop/paste file paths in the "
            "composer. Enter submits; Shift+Enter inserts newline."
        )

    def request_text(self) -> str:
        """Return current composer text."""
        return self.query_one("#iterate-input", IterationComposerInput).text.strip()

    def clear_request(self) -> None:
        """Clear composer text."""
        self.query_one("#iterate-input", IterationComposerInput).clear()

    def focus_input(self) -> None:
        """Focus the composer input."""
        self.query_one("#iterate-input", IterationComposerInput).focus()

    def set_status(self, message: str) -> None:
        """Update the status line."""
        self.query_one("#iterate-status", Static).update(message)

    def set_attachments(self, attachments: list[IterationAttachment]) -> None:
        """Render detected/ingested attachments."""
        label = self.query_one("#iterate-attachments", Static)
        if not attachments:
            label.update("Attachments: none")
            return

        lines = ["Attachments:"]
        for item in attachments:
            lines.append(f"- {item.relative_path} ({item.size_bytes} bytes)")
        label.update("\n".join(lines))


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
        Binding("ctrl+enter", "submit_iteration", "Submit", show=True),
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
        self._iteration_submit_in_progress = False

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
        self.query_one("#iterate-panel", IteratePanel).focus_input()

    def _set_thinking(self, thinking: bool) -> None:
        """Set header thinking indicator."""
        if thinking:
            self.query_one(StatusHeader).set_thinking(True)
        else:
            self.query_one(StatusHeader).set_normal()

    def _set_iteration_busy(self, busy: bool) -> None:
        """Update local in-flight submission state."""
        self._iteration_submit_in_progress = busy

    def _set_iterate_status(self, message: str) -> None:
        """Update iterate panel status text."""
        self.query_one("#iterate-panel", IteratePanel).set_status(message)

    def _set_iterate_attachments(self, attachments: list[IterationAttachment]) -> None:
        """Update iterate panel attachments section."""
        self.query_one("#iterate-panel", IteratePanel).set_attachments(attachments)

    def _switch_to_fly_ready(self) -> None:
        """Transition back to FLY with updated plan."""
        self.coordinator.transition(
            JourneyState.FLY_READY,
            reason="land.iteration_submit",
        )
        self.flight_plan = self.coordinator.flight_plan
        self.waypoints_app.switch_phase(
            "fly",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
            },
        )

    def action_submit_iteration(self) -> None:
        """Submit current iterate composer request."""
        if self.current_activity != LandActivity.ITERATE:
            return
        if self._iteration_submit_in_progress:
            self.notify("Iteration request already in progress", severity="warning")
            return

        panel = self.query_one("#iterate-panel", IteratePanel)
        request_text = panel.request_text()
        if not request_text:
            self.notify("Enter a request before submitting", severity="warning")
            panel.focus_input()
            return

        panel.set_status("Analyzing request and preparing waypoint...")
        self._set_iteration_busy(True)
        self._generate_iteration_waypoint(request_text)

    def on_iteration_submit_requested(self, event: IterationSubmitRequested) -> None:
        """Handle Enter submit from iterate composer input."""
        if self.current_activity != LandActivity.ITERATE:
            return
        if not event.text.strip():
            return
        self.action_submit_iteration()

    @work(thread=True)
    def _generate_iteration_waypoint(self, request_text: str) -> None:
        """Generate a patch waypoint from iterate composer input."""
        self.app.call_from_thread(self._set_thinking, True)
        service = IterationRequestService(self.project.get_path())
        request_writer = IterationRequestWriter(self.project)
        request_id: str | None = None

        try:
            attachments = service.ingest_attachments(request_text)
            triage = service.classify_request(request_text, attachments)
            request_record = request_writer.log_submitted(
                prompt=request_text.strip(),
                triage=triage,
                attachments=[item.to_record() for item in attachments],
            )
            request_id = request_record.request_id
            description = service.build_waypoint_description(
                request_text,
                attachments,
                triage=triage,
            )
            spec_summary = self.spec or None
            waypoint, insert_after = self.coordinator.generate_waypoint(
                description=description,
                spec_summary=spec_summary,
            )
            request_writer.log_waypoint_drafted(
                request_id=request_id,
                draft_waypoint_id=waypoint.id,
                insert_after=insert_after,
            )

            self.app.call_from_thread(self._set_iterate_attachments, attachments)
            triage_intent = triage.intent.value.replace("_", " ")
            self.app.call_from_thread(
                self._set_iterate_status,
                "Triage: "
                f"{triage_intent} ({triage.confidence:.2f}) · "
                "Waypoint draft generated. Review and confirm.",
            )
            self.app.call_from_thread(self.waypoints_app.update_header_cost)
            self.app.call_from_thread(
                self._show_iteration_preview,
                waypoint,
                insert_after,
                request_id,
            )
        except Exception as e:
            logger.exception("Failed to submit iteration request: %s", e)
            if request_id is not None:
                try:
                    request_writer.log_generation_failed(request_id, str(e))
                except Exception:
                    logger.exception(
                        "Failed to persist iteration generation failure for %s",
                        request_id,
                    )
            self.app.call_from_thread(
                self._set_iterate_status,
                f"Failed: {e}",
            )
            self.app.call_from_thread(
                self.notify, f"Could not generate waypoint: {e}", severity="error"
            )
            self.app.call_from_thread(self._set_iteration_busy, False)
            self.app.call_from_thread(self._set_thinking, False)

    def _show_iteration_preview(
        self,
        waypoint: Waypoint,
        insert_after: str | None,
        request_id: str | None,
    ) -> None:
        """Show review modal for generated iteration waypoint."""
        request_writer = IterationRequestWriter(self.project)

        def handle_confirm(confirmed: bool | None) -> None:
            self._set_iteration_busy(False)
            self._set_thinking(False)
            if not confirmed:
                if request_id is not None:
                    try:
                        request_writer.log_cancelled(request_id)
                    except Exception:
                        logger.exception(
                            "Failed to persist cancellation for iteration request %s",
                            request_id,
                        )
                self.query_one("#iterate-panel", IteratePanel).set_status(
                    "Cancelled. Update prompt and submit again."
                )
                return

            self.coordinator.add_waypoint(waypoint, insert_after)
            if request_id is not None:
                try:
                    request_writer.log_waypoint_added(
                        request_id=request_id,
                        waypoint_id=waypoint.id,
                        insert_after=insert_after,
                    )
                except Exception:
                    logger.exception(
                        "Failed to persist waypoint linkage for iteration request %s",
                        request_id,
                    )
            self.flight_plan = self.coordinator.flight_plan
            panel = self.query_one("#iterate-panel", IteratePanel)
            panel.set_status(
                f"Added {waypoint.id}. Returning to Fly to run this iteration."
            )
            panel.clear_request()
            self.notify(f"Added iteration waypoint: {waypoint.id}")
            self._switch_to_fly_ready()

        self.app.push_screen(
            AddWaypointPreviewModal(waypoint, insert_after),
            handle_confirm,
        )

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
