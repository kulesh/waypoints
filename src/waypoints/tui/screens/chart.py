"""Chart screen for waypoint planning (CHART phase)."""

import json
import logging
import re
from typing import TYPE_CHECKING, Any, cast

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.llm.client import ChatClient, StreamChunk, StreamComplete
from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader, FlightPlanWriter
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.flight_plan import (
    BreakDownPreviewModal,
    ConfirmDeleteModal,
    FlightPlanPanel,
    WaypointDetailModal,
    WaypointEditModal,
    WaypointOpenDetail,
    WaypointPreviewPanel,
    WaypointRequestBreakDown,
    WaypointRequestDelete,
    WaypointRequestEdit,
    WaypointSelected,
)
from waypoints.tui.widgets.header import StatusHeader

logger = logging.getLogger(__name__)

WAYPOINT_BREAKDOWN_PROMPT = """\
Break down the following waypoint into 2-5 smaller sub-waypoints.

Parent Waypoint:
- ID: {parent_id}
- Title: {title}
- Objective: {objective}
- Acceptance Criteria: {criteria}

Each sub-waypoint should:
1. Be independently testable
2. Have clear acceptance criteria
3. Be appropriately sized (1-3 hours of focused work)
4. Together fully cover the parent waypoint's objective

Output as a JSON array. Each sub-waypoint has:
- id: String like "{parent_id}a", "{parent_id}b", etc.
- title: Brief descriptive title
- objective: What this sub-waypoint accomplishes
- acceptance_criteria: Array of testable criteria
- parent_id: "{parent_id}" (the parent waypoint ID)
- dependencies: Array of sibling waypoint IDs this depends on (or empty)

Output ONLY the JSON array, no markdown code blocks or other text.

Generate the sub-waypoints JSON now:"""

WAYPOINT_GENERATION_PROMPT = """\
Based on the Product Specification below, generate a flight plan of waypoints
for building this product incrementally.

Each waypoint should:
1. Be independently testable
2. Have clear acceptance criteria
3. Be appropriately sized (1-3 hours of focused work for single-hop)
4. Use parent_id for multi-hop waypoints (epics that contain sub-tasks)

Output as a JSON array of waypoints. Each waypoint has:
- id: String like "WP-001" (use "WP-001a", "WP-001b" for children)
- title: Brief descriptive title
- objective: What this waypoint accomplishes
- acceptance_criteria: Array of testable criteria
- parent_id: ID of parent waypoint (null for top-level)
- dependencies: Array of waypoint IDs this depends on

Generate 8-15 waypoints for MVP scope. Group related work into epics where appropriate.

Output ONLY the JSON array, no markdown code blocks or other text.

Product Specification:
{spec}

Generate the waypoints JSON now:"""


class ChartScreen(Screen[None]):
    """
    Chart screen - Waypoint planning phase.

    Two-panel layout:
    - Left: Flight plan tree view
    - Right: Selected waypoint preview
    - Enter: Opens detail modal
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+enter", "proceed", "Takeoff", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("e", "edit_waypoint", "Edit", show=True),
        Binding("b", "break_down", "Break Down", show=True),
        Binding("d", "delete_waypoint", "Delete", show=True),
        Binding("tab", "switch_panel", "Switch Panel", show=False),
        Binding("ctrl+f", "forward", "Forward", show=True),
        Binding("?", "help", "Help", show=True),
    ]

    DEFAULT_CSS = """
    ChartScreen {
        background: $surface;
        overflow: hidden;
    }

    ChartScreen .main-container {
        width: 100%;
        height: 1fr;
    }

    ChartScreen .generating {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    ChartScreen .generating Static {
        color: $text-muted;
    }

    ChartScreen .file-path {
        dock: bottom;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def __init__(
        self,
        project: Project,
        spec: str,
        idea: str | None = None,
        brief: str | None = None,
        history: DialogueHistory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.spec = spec
        self.idea = idea
        self.brief = brief
        self.history = history
        self.flight_plan: FlightPlan | None = None
        self.llm_client: ChatClient | None = None
        self.file_path = project.get_path() / "flight-plan.jsonl"
        self._active_panel = "left"

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        with Horizontal(classes="main-container"):
            with Vertical(classes="generating", id="generating-view"):
                yield ThinkingIndicator()
                yield Static("Generating flight plan...")
            yield FlightPlanPanel(id="flight-plan-panel")
            yield WaypointPreviewPanel(id="preview-panel")
        yield Static(str(self.file_path), classes="file-path", id="file-path")
        yield Footer()

    def on_mount(self) -> None:
        """Load or generate flight plan."""
        self.app.sub_title = f"{self.project.name} · Chart"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Create ChatClient with metrics collector
        self.llm_client = ChatClient(
            metrics_collector=self.waypoints_app.metrics_collector,
            phase="chart",
        )

        # Hide panels initially
        self.query_one("#flight-plan-panel").display = False
        self.query_one("#preview-panel").display = False

        # Try to load existing flight plan
        self.flight_plan = FlightPlanReader.load(self.project)

        if self.flight_plan and self.flight_plan.waypoints:
            # Show existing plan - already in CHART_REVIEW state
            self._show_panels()
            self._update_panels()
            logger.info(
                "Loaded existing flight plan with %d waypoints",
                len(self.flight_plan.waypoints),
            )
        else:
            # Generate new plan
            # Transition journey state: SHAPE_SPEC_REVIEW -> CHART_GENERATING
            self.project.transition_journey(JourneyState.CHART_GENERATING)
            self._generate_waypoints()

    def _show_panels(self) -> None:
        """Show the panels and hide the generating view."""
        self.query_one("#generating-view").display = False
        self.query_one("#flight-plan-panel").display = True
        self.query_one("#preview-panel").display = True

    def _set_thinking(self, thinking: bool) -> None:
        """Set the header status indicator to thinking state."""
        self.query_one(StatusHeader).set_thinking(thinking)

    @work(thread=True)
    def _generate_waypoints(self) -> None:
        """Generate waypoints from product spec via LLM."""
        assert self.llm_client is not None, "llm_client not initialized"

        prompt = WAYPOINT_GENERATION_PROMPT.format(spec=self.spec)

        logger.info("Generating waypoints from spec: %d chars", len(self.spec))

        self.app.call_from_thread(self._set_thinking, True)

        full_response = ""

        try:
            system_prompt = (
                "You are a technical project planner. Create clear, testable "
                "waypoints for software development. Output valid JSON only."
            )
            for result in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            ):
                if isinstance(result, StreamChunk):
                    full_response += result.text
                elif isinstance(result, StreamComplete):
                    # Update header cost display
                    self.app.call_from_thread(self.waypoints_app.update_header_cost)

            # Parse waypoints from response
            waypoints = self._parse_waypoints(full_response)

            # Create flight plan
            self.flight_plan = FlightPlan(waypoints=waypoints)

            # Save to disk
            writer = FlightPlanWriter(self.project)
            writer.save(self.flight_plan)

            self.app.call_from_thread(self._finalize_generation)

        except Exception as e:
            logger.exception("Error generating waypoints: %s", e)
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

        self.app.call_from_thread(self._set_thinking, False)

    def _parse_waypoints(self, response: str) -> list[Waypoint]:
        """Parse waypoints from LLM response."""
        # Try to extract JSON array from response
        # Handle potential markdown code blocks
        json_match = re.search(r"\[[\s\S]*\]", response)
        if not json_match:
            raise ValueError("No JSON array found in response")

        json_str = json_match.group()
        data = json.loads(json_str)

        waypoints = []
        for item in data:
            wp = Waypoint(
                id=item["id"],
                title=item["title"],
                objective=item["objective"],
                acceptance_criteria=item.get("acceptance_criteria", []),
                parent_id=item.get("parent_id"),
                dependencies=item.get("dependencies", []),
                status=WaypointStatus.PENDING,
            )
            waypoints.append(wp)

        logger.info("Parsed %d waypoints from LLM response", len(waypoints))
        return waypoints

    def _finalize_generation(self) -> None:
        """Finalize after generation completes."""
        self._show_panels()
        self._update_panels()

        # Transition journey state: CHART_GENERATING -> CHART_REVIEW
        self.project.transition_journey(JourneyState.CHART_REVIEW)

        self.notify("Flight plan generated!", severity="information")
        logger.info("Flight plan generation complete")

    def _update_panels(self) -> None:
        """Update both panels with current flight plan."""
        if not self.flight_plan:
            return

        plan_panel = self.query_one("#flight-plan-panel", FlightPlanPanel)
        plan_panel.update_flight_plan(self.flight_plan)

        # Select first waypoint if none selected
        if plan_panel.selected_id is None and self.flight_plan.waypoints:
            plan_panel.select_first()

        # Focus the flight plan panel
        plan_panel.focus()

    def on_waypoint_selected(self, event: WaypointSelected) -> None:
        """Handle waypoint selection."""
        if not self.flight_plan:
            return

        waypoint = self.flight_plan.get_waypoint(event.waypoint_id)
        is_epic = self.flight_plan.is_epic(event.waypoint_id) if waypoint else False
        preview_panel = self.query_one("#preview-panel", WaypointPreviewPanel)
        preview_panel.show_waypoint(waypoint, is_epic)

    def on_waypoint_open_detail(self, event: WaypointOpenDetail) -> None:
        """Handle request to open waypoint detail modal."""
        if not self.flight_plan:
            return

        waypoint = self.flight_plan.get_waypoint(event.waypoint_id)
        if waypoint:
            is_epic = self.flight_plan.is_epic(event.waypoint_id)
            self.app.push_screen(WaypointDetailModal(waypoint, is_epic))

    def action_switch_panel(self) -> None:
        """Switch focus between panels."""
        self._active_panel = "right" if self._active_panel == "left" else "left"
        if self._active_panel == "left":
            self.query_one("#flight-plan-panel").focus()
        else:
            self.query_one("#preview-panel").focus()

    def action_edit_waypoint(self) -> None:
        """Edit the selected waypoint."""
        plan_panel = self.query_one("#flight-plan-panel", FlightPlanPanel)
        if plan_panel.selected_id and self.flight_plan:
            waypoint = self.flight_plan.get_waypoint(plan_panel.selected_id)
            if waypoint:
                self._show_edit_modal(waypoint)

    def on_waypoint_request_edit(self, event: WaypointRequestEdit) -> None:
        """Handle edit request from detail modal."""
        self._show_edit_modal(event.waypoint)

    def _show_edit_modal(self, waypoint: Waypoint) -> None:
        """Show edit modal for a waypoint."""

        def handle_edit(updated: Waypoint | None) -> None:
            if updated:
                self._save_waypoint(updated)

        self.app.push_screen(WaypointEditModal(waypoint), handle_edit)

    def _save_waypoint(self, waypoint: Waypoint) -> None:
        """Save an updated waypoint."""
        if not self.flight_plan:
            return

        self.flight_plan.update_waypoint(waypoint)

        # Save to disk
        writer = FlightPlanWriter(self.project)
        writer.save(self.flight_plan)

        # Refresh the tree
        self._update_panels()
        self.notify(f"Updated {waypoint.id}")

    def action_break_down(self) -> None:
        """Break down selected waypoint into sub-waypoints."""
        plan_panel = self.query_one("#flight-plan-panel", FlightPlanPanel)
        if plan_panel.selected_id and self.flight_plan:
            waypoint = self.flight_plan.get_waypoint(plan_panel.selected_id)
            if waypoint:
                self._start_break_down(waypoint)

    def on_waypoint_request_break_down(self, event: WaypointRequestBreakDown) -> None:
        """Handle break down request from detail modal."""
        self._start_break_down(event.waypoint)

    def _start_break_down(self, waypoint: Waypoint) -> None:
        """Start the break down process for a waypoint."""
        # Check if already an epic
        if self.flight_plan and self.flight_plan.is_epic(waypoint.id):
            self.notify(f"{waypoint.id} already has sub-waypoints", severity="warning")
            return

        self.notify(f"Breaking down {waypoint.id}...")
        self._generate_sub_waypoints(waypoint)

    @work(thread=True)
    def _generate_sub_waypoints(self, parent: Waypoint) -> None:
        """Generate sub-waypoints via LLM."""
        assert self.llm_client is not None, "llm_client not initialized"

        criteria_str = "\n".join(f"- {c}" for c in parent.acceptance_criteria)
        if not criteria_str:
            criteria_str = "(none specified)"

        prompt = WAYPOINT_BREAKDOWN_PROMPT.format(
            parent_id=parent.id,
            title=parent.title,
            objective=parent.objective,
            criteria=criteria_str,
        )

        self.app.call_from_thread(self._set_thinking, True)

        try:
            system_prompt = (
                "You are a technical project planner. Break down waypoints into "
                "smaller, independently testable tasks. Output valid JSON only."
            )

            full_response = ""
            for result in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            ):
                if isinstance(result, StreamChunk):
                    full_response += result.text
                elif isinstance(result, StreamComplete):
                    # Update header cost display
                    self.app.call_from_thread(self.waypoints_app.update_header_cost)

            # Parse the sub-waypoints
            sub_waypoints = self._parse_waypoints(full_response)

            # Ensure all have correct parent_id
            for wp in sub_waypoints:
                wp.parent_id = parent.id

            # Show preview modal
            self.app.call_from_thread(
                self._show_break_down_preview, parent, sub_waypoints
            )

        except Exception as e:
            logger.exception("Error generating sub-waypoints: %s", e)
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

        self.app.call_from_thread(self._set_thinking, False)

    def _show_break_down_preview(
        self, parent: Waypoint, sub_waypoints: list[Waypoint]
    ) -> None:
        """Show the break down preview modal."""
        if not sub_waypoints:
            self.notify("No sub-waypoints generated", severity="warning")
            return

        def handle_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._add_sub_waypoints(parent, sub_waypoints)

        self.app.push_screen(
            BreakDownPreviewModal(parent, sub_waypoints),
            handle_confirm,
        )

    def _add_sub_waypoints(
        self, parent: Waypoint, sub_waypoints: list[Waypoint]
    ) -> None:
        """Add sub-waypoints to the flight plan."""
        if not self.flight_plan:
            return

        # Insert after parent to maintain selection order
        self.flight_plan.insert_waypoints_after(parent.id, sub_waypoints)

        # Save to disk
        writer = FlightPlanWriter(self.project)
        writer.save(self.flight_plan)

        # Refresh the tree
        self._update_panels()
        self.notify(f"Added {len(sub_waypoints)} sub-waypoints to {parent.id}")

    def action_delete_waypoint(self) -> None:
        """Delete selected waypoint."""
        plan_panel = self.query_one("#flight-plan-panel", FlightPlanPanel)
        if plan_panel.selected_id and self.flight_plan:
            self._show_delete_confirmation(plan_panel.selected_id)

    def on_waypoint_request_delete(self, event: WaypointRequestDelete) -> None:
        """Handle delete request from detail modal."""
        self._show_delete_confirmation(event.waypoint_id)

    def _show_delete_confirmation(self, waypoint_id: str) -> None:
        """Show delete confirmation modal for a waypoint."""
        if not self.flight_plan:
            return

        waypoint = self.flight_plan.get_waypoint(waypoint_id)
        if not waypoint:
            return

        has_children = self.flight_plan.is_epic(waypoint_id)
        dependents = self.flight_plan.get_dependents(waypoint_id)
        dependent_ids = [wp.id for wp in dependents]

        def handle_delete(confirmed: bool | None) -> None:
            if confirmed:
                self._delete_waypoint(waypoint_id)

        self.app.push_screen(
            ConfirmDeleteModal(
                waypoint_id=waypoint_id,
                waypoint_title=waypoint.title,
                has_children=has_children,
                dependents=dependent_ids,
            ),
            handle_delete,
        )

    def _delete_waypoint(self, waypoint_id: str) -> None:
        """Actually delete the waypoint and refresh UI."""
        if not self.flight_plan:
            return

        self.flight_plan.remove_waypoint(waypoint_id)

        # Save to disk
        writer = FlightPlanWriter(self.project)
        writer.save(self.flight_plan)

        # Refresh the tree
        self._update_panels()
        self.notify(f"Deleted {waypoint_id}")

    def action_help(self) -> None:
        """Show help overlay."""
        self.notify(
            "j/k: Navigate | Enter: Details | e: Edit | b: Break down | d: Delete"
        )

    def action_proceed(self) -> None:
        """Proceed to FLY phase (Ready for Takeoff)."""
        if not self.flight_plan:
            self.notify("No flight plan to execute", severity="error")
            return

        errors = self.flight_plan.validate_dependencies()
        if errors:
            self.notify(f"Fix issues: {errors[0]}", severity="error")
            return

        # Transition journey state: CHART_REVIEW -> FLY_READY
        self.project.transition_journey(JourneyState.FLY_READY)

        # Transition to FLY phase
        self.waypoints_app.switch_phase(
            "fly",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
                "from_phase": "chart",
            },
        )

    def action_back(self) -> None:
        """Go back to Product Spec screen."""
        from waypoints.tui.screens.product_spec import ProductSpecResumeScreen

        # Load spec and brief from disk to ensure we have content
        spec = self.app._load_latest_doc(self.project, "product-spec")  # type: ignore[attr-defined]
        brief = self.app._load_latest_doc(self.project, "idea-brief")  # type: ignore[attr-defined]
        self.app.switch_screen(
            ProductSpecResumeScreen(
                project=self.project, spec=spec or self.spec, brief=brief
            )
        )

    def action_forward(self) -> None:
        """Go forward to Fly screen (if flight plan exists)."""
        from waypoints.tui.screens.fly import FlyScreen

        if not self.flight_plan:
            self.notify("No flight plan yet. Press Ctrl+Enter to takeoff.")
            return

        # Transition to FLY_READY if currently in CHART_REVIEW
        journey = self.project.journey
        if journey and journey.state == JourneyState.CHART_REVIEW:
            self.project.transition_journey(JourneyState.FLY_READY)

        self.app.switch_screen(
            FlyScreen(
                project=self.project,
                flight_plan=self.flight_plan,
                spec=self.spec,
            )
        )
