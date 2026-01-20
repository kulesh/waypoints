"""Chart screen for waypoint planning (CHART phase)."""

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.llm.validation import WaypointValidationError
from waypoints.models import JourneyState, Project
from waypoints.models.dialogue import DialogueHistory
from waypoints.models.flight_plan import FlightPlan, FlightPlanReader
from waypoints.models.waypoint import Waypoint
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.widgets.dialogue import ThinkingIndicator
from waypoints.tui.widgets.flight_plan import (
    AddWaypointModal,
    AddWaypointPreviewModal,
    BreakDownPreviewModal,
    ConfirmDeleteModal,
    FlightPlanPanel,
    ReprioritizePreviewModal,
    WaypointDetailModal,
    WaypointOpenDetail,
    WaypointPreviewPanel,
    WaypointRequestBreakDown,
    WaypointRequestDelete,
    WaypointRequestEdit,
    WaypointSelected,
)
from waypoints.tui.widgets.header import StatusHeader
from waypoints.tui.widgets.resizable_split import ResizableSplit

logger = logging.getLogger(__name__)


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
        Binding("escape", "back", "Back", show=False),
        Binding("a", "add_waypoint", "Add", show=True),
        Binding("R", "reprioritize", "Reprioritize", show=True),
        Binding("e", "edit_waypoint", "Edit", show=False),
        Binding("b", "break_down", "Break Down", show=False),
        Binding("d", "delete_waypoint", "Delete", show=False),
        Binding("tab", "switch_panel", "Switch Panel", show=False),
        Binding("ctrl+f", "forward", "Forward", show=False),
        Binding("?", "help", "Help", show=False),
        Binding("comma", "shrink_left", "< Pane", show=True),
        Binding("full_stop", "expand_left", "> Pane", show=True),
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
        height: 1fr;
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
        coordinator: JourneyCoordinator | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.spec = spec
        self.idea = idea
        self.brief = brief
        self.history = history
        self.flight_plan: FlightPlan | None = None
        self.file_path = project.get_path() / "flight-plan.jsonl"
        self._active_panel = "left"

        # Coordinator will be set up in on_mount after flight_plan is loaded
        self._coordinator: JourneyCoordinator | None = coordinator

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    @property
    def coordinator(self) -> JourneyCoordinator:
        """Get the coordinator, creating if needed."""
        if self._coordinator is None:
            self._coordinator = JourneyCoordinator(
                project=self.project,
                flight_plan=self.flight_plan,
                metrics=self.waypoints_app.metrics_collector,
            )
        return self._coordinator

    def _sync_coordinator_flight_plan(self) -> None:
        """Sync coordinator's flight plan with screen's."""
        if self._coordinator is not None and self.flight_plan is not None:
            self._coordinator.flight_plan = self.flight_plan

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        with Vertical(classes="generating", id="generating-view"):
            yield ThinkingIndicator()
            yield Static("Generating flight plan...")
        yield ResizableSplit(
            left=FlightPlanPanel(id="flight-plan-panel"),
            right=WaypointPreviewPanel(id="preview-panel"),
            left_pct=40,
            classes="main-container",
        )
        yield Static(str(self.file_path), classes="file-path", id="file-path")
        yield Footer()

    def on_mount(self) -> None:
        """Load or generate flight plan."""
        self.app.sub_title = f"{self.project.name} · Chart"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Hide main container initially (show generating view)
        self.query_one(".main-container").display = False

        # Try to load existing flight plan
        self.flight_plan = FlightPlanReader.load(self.project)

        if self.flight_plan and self.flight_plan.waypoints:
            # Show existing plan - already in CHART_REVIEW state
            self._sync_coordinator_flight_plan()
            self._show_panels()
            self._update_panels()
            logger.info(
                "Loaded existing flight plan with %d waypoints",
                len(self.flight_plan.waypoints),
            )
        else:
            # Generate new plan - but first verify we can transition
            # Expected: SHAPE_SPEC_REVIEW -> CHART_GENERATING
            journey = self.project.journey
            if journey and not journey.can_transition(JourneyState.CHART_GENERATING):
                # Not in expected state (e.g., after crash recovery to earlier state)
                # Redirect to appropriate screen for current phase
                logger.warning(
                    "Cannot generate chart from state %s, redirecting to phase %s",
                    journey.state.value,
                    journey.phase,
                )
                self.notify(
                    f"Redirecting to {journey.phase} (recovery state)",
                    severity="warning",
                )
                self.app.call_later(self._redirect_to_current_phase)
                return

            self.project.transition_journey(JourneyState.CHART_GENERATING)
            self._generate_waypoints()

    def _show_panels(self) -> None:
        """Show the panels and hide the generating view."""
        self.query_one("#generating-view").display = False
        self.query_one(".main-container").display = True

    def _redirect_to_current_phase(self) -> None:
        """Redirect to the screen matching current journey phase."""
        journey = self.project.journey
        if not journey:
            return

        # Let the app handle routing to the correct screen
        self.waypoints_app._resume_project(self.project)

    def _set_thinking(self, thinking: bool) -> None:
        """Set the header status indicator to thinking state."""
        self.query_one(StatusHeader).set_thinking(thinking)

    @work(thread=True)
    def _generate_waypoints(self) -> None:
        """Generate waypoints from product spec via coordinator."""
        logger.info("Generating waypoints from spec: %d chars", len(self.spec))

        self.app.call_from_thread(self._set_thinking, True)

        try:
            # Coordinator generates flight plan, saves to disk, and logs to audit
            flight_plan = self.coordinator.generate_flight_plan(spec=self.spec)
            self.flight_plan = flight_plan

            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            self.app.call_from_thread(self._finalize_generation)

        except Exception as e:
            logger.exception("Error generating waypoints: %s", e)
            self.app.call_from_thread(self.notify, f"Error: {e}", severity="error")

        self.app.call_from_thread(self._set_thinking, False)

    def _finalize_generation(self) -> None:
        """Finalize after generation completes."""
        # Sync coordinator with newly generated flight plan
        self._sync_coordinator_flight_plan()

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

            def handle_modal_result(action: str | None) -> None:
                if action == "edit":
                    self._edit_waypoint_external(waypoint, reopen_modal=True)
                elif action == "break_down":
                    self._start_break_down(waypoint)
                elif action == "delete":
                    self._show_delete_confirmation(waypoint.id)

            self.app.push_screen(
                WaypointDetailModal(waypoint, is_epic), handle_modal_result
            )

    def action_switch_panel(self) -> None:
        """Switch focus between panels."""
        self._active_panel = "right" if self._active_panel == "left" else "left"
        if self._active_panel == "left":
            self.query_one("#flight-plan-panel").focus()
        else:
            self.query_one("#preview-panel").focus()

    def action_edit_waypoint(self) -> None:
        """Edit the selected waypoint in external editor as YAML."""
        plan_panel = self.query_one("#flight-plan-panel", FlightPlanPanel)
        if plan_panel.selected_id and self.flight_plan:
            waypoint = self.flight_plan.get_waypoint(plan_panel.selected_id)
            if waypoint:
                self._edit_waypoint_external(waypoint)

    def on_waypoint_request_edit(self, event: WaypointRequestEdit) -> None:
        """Handle edit request from detail modal."""
        self._edit_waypoint_external(event.waypoint)

    def _edit_waypoint_external(
        self, waypoint: Waypoint, reopen_modal: bool = False
    ) -> None:
        """Edit waypoint in external editor as YAML."""
        from waypoints.tui.utils import edit_file_in_editor

        # Serialize waypoint to human-readable YAML
        data = {
            "id": waypoint.id,
            "title": waypoint.title,
            "objective": waypoint.objective,
            "acceptance_criteria": waypoint.acceptance_criteria,
        }

        # Write to temp file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=f"-{waypoint.id}.yaml",
            delete=False,
            prefix="waypoint-",
        ) as f:
            f.write(f"# Editing waypoint {waypoint.id}\n")
            f.write("# Save and close to apply changes\n\n")
            yaml.dump(
                data, f, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
            temp_path = Path(f.name)

        def on_complete() -> None:
            self._apply_waypoint_edits(waypoint, temp_path, reopen_modal)

        if not edit_file_in_editor(self.app, temp_path, on_complete):
            self.notify(
                "Editor not allowed. Set $EDITOR to vim, code, etc.",
                severity="error",
            )
            # Clean up temp file on failure
            temp_path.unlink(missing_ok=True)

    def _apply_waypoint_edits(
        self, waypoint: Waypoint, yaml_path: Path, reopen_modal: bool = False
    ) -> None:
        """Parse YAML and update waypoint."""
        try:
            data = yaml.safe_load(yaml_path.read_text())
            if not data:
                self.notify("Empty file, no changes applied", severity="warning")
                return

            # Update waypoint fields (but not id)
            waypoint.title = data.get("title", waypoint.title)
            waypoint.objective = data.get("objective", waypoint.objective)
            waypoint.acceptance_criteria = data.get("acceptance_criteria", [])

            # Save via coordinator
            self.coordinator.update_waypoint(waypoint)

            # Refresh display
            self._update_panels()

            # Also refresh the preview panel with updated waypoint
            is_epic = (
                self.flight_plan.is_epic(waypoint.id) if self.flight_plan else False
            )
            preview_panel = self.query_one("#preview-panel", WaypointPreviewPanel)
            preview_panel.show_waypoint(waypoint, is_epic)

            self.notify(f"Updated {waypoint.id}")

            # Reopen modal if requested (when editing from modal)
            if reopen_modal:
                self._reopen_waypoint_modal(waypoint)
        except yaml.YAMLError as e:
            self.notify(f"Invalid YAML: {e}", severity="error")
        except Exception as e:
            self.notify(f"Error applying edits: {e}", severity="error")
        finally:
            # Clean up temp file
            try:
                yaml_path.unlink()
            except OSError:
                pass

    def _reopen_waypoint_modal(self, waypoint: Waypoint) -> None:
        """Reopen the waypoint detail modal after editing."""
        if not self.flight_plan:
            return

        is_epic = self.flight_plan.is_epic(waypoint.id)

        def handle_modal_result(action: str | None) -> None:
            if action == "edit":
                self._edit_waypoint_external(waypoint, reopen_modal=True)
            elif action == "break_down":
                self._start_break_down(waypoint)
            elif action == "delete":
                self._show_delete_confirmation(waypoint.id)

        self.app.push_screen(
            WaypointDetailModal(waypoint, is_epic), handle_modal_result
        )

    def _save_waypoint(self, waypoint: Waypoint) -> None:
        """Save an updated waypoint."""
        if not self.flight_plan:
            return

        # Delegate to coordinator
        self.coordinator.update_waypoint(waypoint)

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
        """Generate sub-waypoints via coordinator."""
        self.app.call_from_thread(self._set_thinking, True)

        try:
            # Coordinator breaks down waypoint and returns sub-waypoints
            sub_waypoints = self.coordinator.break_down_waypoint(waypoint=parent)

            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            # Show preview modal
            self.app.call_from_thread(
                self._show_break_down_preview, parent, sub_waypoints
            )

        except ValueError as e:
            # Waypoint is already an epic
            self.app.call_from_thread(self.notify, str(e), severity="warning")
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

        # Delegate to coordinator
        self.coordinator.add_sub_waypoints(parent.id, sub_waypoints)

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

        # Delegate to coordinator
        self.coordinator.delete_waypoint(waypoint_id)

        # Refresh the tree
        self._update_panels()
        self.notify(f"Deleted {waypoint_id}")

    # ─── Add Waypoint ────────────────────────────────────────────────────

    def action_add_waypoint(self) -> None:
        """Show modal to add a new waypoint."""
        if not self.flight_plan:
            self.notify("No flight plan loaded", severity="error")
            return

        def handle_result(description: str | None) -> None:
            if description:
                self._generate_new_waypoint(description)

        self.app.push_screen(AddWaypointModal(), handle_result)

    @work(thread=True)
    def _generate_new_waypoint(self, description: str) -> None:
        """Generate a waypoint from user description via coordinator."""
        assert self.flight_plan is not None

        self.app.call_from_thread(
            self.notify, "Generating waypoint...", severity="information"
        )

        # Truncate spec for prompt context
        spec_summary = self.spec[:2000] if self.spec else None

        try:
            # Coordinator generates waypoint and returns it with insert position
            waypoint, insert_after = self.coordinator.generate_waypoint(
                description=description,
                spec_summary=spec_summary,
            )

            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            # Show preview modal
            self.app.call_from_thread(
                self._show_waypoint_preview, waypoint, insert_after
            )

        except WaypointValidationError as e:
            logger.error("Waypoint validation failed: %s", e)
            self.app.call_from_thread(
                self.notify,
                f"Invalid waypoint: {e}",
                severity="error",
            )
        except Exception as e:
            logger.exception("Error generating waypoint: %s", e)
            self.app.call_from_thread(
                self.notify, f"Error generating waypoint: {e}", severity="error"
            )

    def _show_waypoint_preview(
        self, waypoint: Waypoint, insert_after: str | None
    ) -> None:
        """Show preview modal for generated waypoint."""

        def handle_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._add_waypoint(waypoint, insert_after)

        self.app.push_screen(
            AddWaypointPreviewModal(waypoint, insert_after),
            handle_confirm,
        )

    def _add_waypoint(self, waypoint: Waypoint, after_id: str | None) -> None:
        """Add waypoint to flight plan and refresh UI."""
        if not self.flight_plan:
            return

        # Delegate to coordinator
        self.coordinator.add_waypoint(waypoint, after_id)

        # Refresh the tree
        self._update_panels()
        self.notify(f"Added waypoint: {waypoint.id}")

    # ─── Reprioritize Waypoints ───────────────────────────────────────────

    def action_reprioritize(self) -> None:
        """Trigger AI reprioritization of waypoints."""
        if not self.flight_plan:
            self.notify("No flight plan loaded", severity="error")
            return

        root_waypoints = self.flight_plan.get_root_waypoints()
        if len(root_waypoints) < 2:
            self.notify("Need at least 2 waypoints to reprioritize", severity="warning")
            return

        self._set_thinking(True)
        self._generate_reprioritization()

    @work(thread=True)
    def _generate_reprioritization(self) -> None:
        """Generate reprioritization suggestion via coordinator."""
        assert self.flight_plan is not None

        root_waypoints = self.flight_plan.get_root_waypoints()
        current_order = [wp.id for wp in root_waypoints]

        # Truncate spec for prompt context
        spec_summary = self.spec[:2000] if self.spec else None

        try:
            # Coordinator suggests reprioritization
            new_order, rationale, changes = self.coordinator.suggest_reprioritization(
                spec_summary=spec_summary,
            )

            # Update header cost display
            self.app.call_from_thread(self.waypoints_app.update_header_cost)

            # Check if order actually changed
            if new_order == current_order:
                self.app.call_from_thread(self._set_thinking, False)
                self.app.call_from_thread(
                    self.notify,
                    f"Order is already optimal: {rationale}",
                    severity="information",
                )
                return

            # Show preview modal
            self.app.call_from_thread(self._set_thinking, False)
            self.app.call_from_thread(
                self._show_reprioritize_preview,
                current_order,
                new_order,
                rationale,
                changes,
            )

        except RuntimeError as e:
            # Not enough waypoints or no flight plan
            self.app.call_from_thread(self._set_thinking, False)
            self.app.call_from_thread(self.notify, str(e), severity="warning")
        except WaypointValidationError as e:
            logger.error("Reprioritization validation failed: %s", e)
            self.app.call_from_thread(self._set_thinking, False)
            self.app.call_from_thread(
                self.notify,
                f"Invalid response: {e}",
                severity="error",
            )
        except Exception as e:
            logger.exception("Error generating reprioritization: %s", e)
            self.app.call_from_thread(self._set_thinking, False)
            self.app.call_from_thread(
                self.notify, f"Error analyzing order: {e}", severity="error"
            )

    def _show_reprioritize_preview(
        self,
        current_order: list[str],
        new_order: list[str],
        rationale: str,
        changes: list[dict[str, str]],
    ) -> None:
        """Show preview modal for reprioritization."""
        if not self.flight_plan:
            return

        # Build waypoint titles map
        waypoint_titles = {
            wp.id: wp.title for wp in self.flight_plan.get_root_waypoints()
        }

        def handle_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._apply_new_order(new_order, rationale, changes)

        self.app.push_screen(
            ReprioritizePreviewModal(
                current_order=current_order,
                new_order=new_order,
                rationale=rationale,
                waypoint_titles=waypoint_titles,
                changes=changes,
            ),
            handle_confirm,
        )

    def _apply_new_order(
        self,
        new_order: list[str],
        rationale: str,
        changes: list[dict[str, str]],
    ) -> None:
        """Apply the new waypoint order."""
        if not self.flight_plan:
            return

        # Delegate to coordinator (handles logging)
        self.coordinator.reorder_waypoints(new_order, rationale, changes)

        # Refresh the tree
        self._update_panels()
        self.notify("Waypoints reprioritized")

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

        # Transition journey state to FLY_READY when valid.
        journey = self.project.journey
        if journey:
            if journey.can_transition(JourneyState.FLY_READY):
                self.project.transition_journey(JourneyState.FLY_READY)
            elif journey.state in (
                JourneyState.FLY_READY,
                JourneyState.FLY_EXECUTING,
                JourneyState.FLY_PAUSED,
                JourneyState.FLY_INTERVENTION,
            ):
                logger.info(
                    "Skipping journey transition: already in fly state %s",
                    journey.state,
                )
            else:
                self.notify(
                    f"Cannot proceed to fly from {journey.state.value}",
                    severity="error",
                )
                return
        else:
            self.project.transition_journey(JourneyState.FLY_READY)

        # Transition to FLY phase
        self.waypoints_app.switch_phase(
            "fly",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
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

    def action_shrink_left(self) -> None:
        """Shrink the left pane."""
        split = self.query_one(ResizableSplit)
        split.left_pct = max(15, split.left_pct - 5)

    def action_expand_left(self) -> None:
        """Expand the left pane."""
        split = self.query_one(ResizableSplit)
        split.left_pct = min(70, split.left_pct + 5)
