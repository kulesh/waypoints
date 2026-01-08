"""Chart screen for waypoint planning (CHART phase)."""

import json
import logging
import re

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from waypoints.llm.client import ChatClient
from waypoints.models import Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader, FlightPlanWriter
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.flight_plan import (
    FlightPlanPanel,
    WaypointDetailModal,
    WaypointOpenDetail,
    WaypointPreviewPanel,
    WaypointSelected,
)
from waypoints.tui.widgets.status_indicator import ModelStatusIndicator

logger = logging.getLogger(__name__)

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


class ChartScreen(Screen):
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
        Binding("e", "edit_waypoint", "Edit", show=True),
        Binding("b", "break_down", "Break Down", show=True),
        Binding("d", "delete_waypoint", "Delete", show=True),
        Binding("tab", "switch_panel", "Switch Panel", show=False),
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

    ChartScreen ModelStatusIndicator {
        dock: top;
        layer: above;
        margin: 0 0 0 1;
        height: 1;
        width: 2;
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
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.spec = spec
        self.idea = idea
        self.brief = brief
        self.history = history
        self.flight_plan: FlightPlan | None = None
        self.llm_client = ChatClient()
        self.file_path = project.get_path() / "flight-plan.jsonl"
        self._active_panel = "left"

    def compose(self) -> ComposeResult:
        yield Header()
        yield ModelStatusIndicator(id="model-status")
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

        # Hide panels initially
        self.query_one("#flight-plan-panel").display = False
        self.query_one("#preview-panel").display = False

        # Try to load existing flight plan
        self.flight_plan = FlightPlanReader.load(self.project)

        if self.flight_plan and self.flight_plan.waypoints:
            # Show existing plan
            self._show_panels()
            self._update_panels()
            logger.info(
                "Loaded existing flight plan with %d waypoints",
                len(self.flight_plan.waypoints),
            )
        else:
            # Generate new plan
            self._generate_waypoints()

    def _show_panels(self) -> None:
        """Show the panels and hide the generating view."""
        self.query_one("#generating-view").display = False
        self.query_one("#flight-plan-panel").display = True
        self.query_one("#preview-panel").display = True

    @work(thread=True)
    def _generate_waypoints(self) -> None:
        """Generate waypoints from product spec via LLM."""
        prompt = WAYPOINT_GENERATION_PROMPT.format(spec=self.spec)

        logger.info("Generating waypoints from spec: %d chars", len(self.spec))

        self.app.call_from_thread(self._set_thinking, True)

        full_response = ""

        try:
            system_prompt = (
                "You are a technical project planner. Create clear, testable "
                "waypoints for software development. Output valid JSON only."
            )
            for chunk in self.llm_client.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=system_prompt,
            ):
                full_response += chunk

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

    def _set_thinking(self, thinking: bool) -> None:
        """Toggle the model status indicator."""
        self.query_one("#model-status", ModelStatusIndicator).set_thinking(thinking)

    def _finalize_generation(self) -> None:
        """Finalize after generation completes."""
        self._show_panels()
        self._update_panels()
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
            plan_panel.selected_id = self.flight_plan.waypoints[0].id

        # Focus the flight plan panel
        plan_panel.focus()

    def on_waypoint_selected(self, event: WaypointSelected) -> None:
        """Handle waypoint selection."""
        if not self.flight_plan:
            return

        waypoint = self.flight_plan.get_waypoint(event.waypoint_id)
        is_epic = (
            self.flight_plan.is_epic(event.waypoint_id) if waypoint else False
        )
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
                is_epic = self.flight_plan.is_epic(plan_panel.selected_id)
                self.app.push_screen(WaypointDetailModal(waypoint, is_epic))

    def action_break_down(self) -> None:
        """Break down selected waypoint into sub-waypoints."""
        self.notify("Break down not yet implemented")

    def action_delete_waypoint(self) -> None:
        """Delete selected waypoint."""
        self.notify("Delete not yet implemented")

    def action_help(self) -> None:
        """Show help overlay."""
        self.notify(
            "j/k: Navigate | Enter: Details | e: Edit | b: Break down | d: Delete"
        )

    def action_proceed(self) -> None:
        """Proceed to next phase (Ready for Takeoff)."""
        if self.flight_plan:
            errors = self.flight_plan.validate_dependencies()
            if errors:
                self.notify(f"Fix issues: {errors[0]}", severity="error")
                return

        # Future: transition to FLY phase
        self.notify("Ready for Takeoff! (FLY phase coming soon)")
