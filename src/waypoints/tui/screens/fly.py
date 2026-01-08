"""FLY screen for waypoint implementation."""

import logging
from datetime import datetime
from enum import Enum

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Static, Tree
from textual.worker import Worker

from waypoints.fly.executor import (
    ExecutionContext,
    ExecutionResult,
    WaypointExecutor,
)
from waypoints.models import Project
from waypoints.models.flight_plan import FlightPlan, FlightPlanWriter
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.widgets.flight_plan import FlightPlanTree
from waypoints.tui.widgets.header import StatusHeader

logger = logging.getLogger(__name__)


class ExecutionState(Enum):
    """State of waypoint execution."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    INTERVENTION = "intervention"


class ExecutionLog(VerticalScroll):
    """Scrollable log of execution output."""

    DEFAULT_CSS = """
    ExecutionLog {
        height: 1fr;
        padding: 1;
        background: $surface;
        scrollbar-gutter: stable;
        scrollbar-size: 1 1;
        scrollbar-background: transparent;
        scrollbar-color: $surface-lighten-2;
    }

    ExecutionLog .log-entry {
        margin-bottom: 0;
    }

    ExecutionLog .log-entry.success {
        color: $success;
    }

    ExecutionLog .log-entry.error {
        color: $error;
    }

    ExecutionLog .log-entry.info {
        color: $text-muted;
    }

    ExecutionLog .log-entry.command {
        color: $warning;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._entries: list[Static] = []

    def log(self, message: str, level: str = "info") -> None:
        """Add a log entry."""
        entry = Static(message, classes=f"log-entry {level}")
        self._entries.append(entry)
        self.mount(entry)
        self.scroll_end(animate=False)

    def log_command(self, command: str) -> None:
        """Log a command being executed."""
        self.log(f"$ {command}", "command")

    def log_success(self, message: str) -> None:
        """Log a success message."""
        self.log(f"✓ {message}", "success")

    def log_error(self, message: str) -> None:
        """Log an error message."""
        self.log(f"✗ {message}", "error")

    def clear_log(self) -> None:
        """Clear all log entries."""
        for entry in self._entries:
            entry.remove()
        self._entries.clear()


class WaypointDetailPanel(Vertical):
    """Right panel showing current waypoint details and execution log."""

    DEFAULT_CSS = """
    WaypointDetailPanel {
        width: 2fr;
        height: 100%;
        padding: 0;
    }

    WaypointDetailPanel .panel-header {
        height: auto;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointDetailPanel .wp-title {
        text-style: bold;
        margin-bottom: 1;
    }

    WaypointDetailPanel .wp-objective {
        color: $text-muted;
        margin-bottom: 1;
    }

    WaypointDetailPanel .wp-status {
        color: $text-muted;
    }

    WaypointDetailPanel .progress-section {
        height: auto;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointDetailPanel .progress-label {
        margin-bottom: 0;
    }

    WaypointDetailPanel .progress-bar {
        color: $success;
    }

    WaypointDetailPanel .log-section {
        height: 1fr;
    }

    WaypointDetailPanel .log-header {
        padding: 1;
        color: $text-muted;
        border-bottom: solid $surface-lighten-1;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._waypoint: Waypoint | None = None
        self._progress: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("Select a waypoint", classes="wp-title", id="wp-title")
            yield Static("", classes="wp-objective", id="wp-objective")
            yield Static("Status: Pending", classes="wp-status", id="wp-status")
        with Vertical(classes="progress-section"):
            yield Static("Progress:", classes="progress-label")
            yield Static("□□□□□□□□□□ 0%", classes="progress-bar", id="progress-bar")
        with Vertical(classes="log-section"):
            yield Static("Output", classes="log-header")
            yield ExecutionLog(id="execution-log")

    def show_waypoint(self, waypoint: Waypoint | None) -> None:
        """Display waypoint details."""
        self._waypoint = waypoint

        title = self.query_one("#wp-title", Static)
        objective = self.query_one("#wp-objective", Static)
        status = self.query_one("#wp-status", Static)

        if waypoint:
            title.update(f"{waypoint.id}: {waypoint.title}")
            obj_text = waypoint.objective
            if len(obj_text) > 100:
                obj_text = obj_text[:97] + "..."
            objective.update(obj_text)
            status_text = waypoint.status.value.replace("_", " ").title()
            status.update(f"Status: {status_text}")
        else:
            title.update("Select a waypoint")
            objective.update("")
            status.update("Status: -")

    def update_progress(self, percent: int) -> None:
        """Update the progress bar."""
        self._progress = percent
        filled = percent // 10
        empty = 10 - filled
        bar = "■" * filled + "□" * empty
        self.query_one("#progress-bar", Static).update(f"{bar} {percent}%")

    @property
    def log(self) -> ExecutionLog:
        """Get the execution log widget."""
        return self.query_one("#execution-log", ExecutionLog)


class WaypointListPanel(Vertical):
    """Left panel showing waypoint list with status."""

    DEFAULT_CSS = """
    WaypointListPanel {
        width: 1fr;
        height: 100%;
        border-right: solid $surface-lighten-1;
    }

    WaypointListPanel .panel-title {
        text-style: bold;
        color: $text;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointListPanel .legend {
        dock: bottom;
        height: auto;
        padding: 1;
        border-top: solid $surface-lighten-1;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._flight_plan: FlightPlan | None = None

    def compose(self) -> ComposeResult:
        yield Static("WAYPOINTS", classes="panel-title")
        yield FlightPlanTree(id="waypoint-tree")
        yield Static("◉ Done  ◎ Active  ○ Pending", classes="legend")

    def update_flight_plan(self, flight_plan: FlightPlan) -> None:
        """Update the waypoint list."""
        self._flight_plan = flight_plan
        tree = self.query_one("#waypoint-tree", FlightPlanTree)
        tree.update_flight_plan(flight_plan)

    @property
    def selected_waypoint(self) -> Waypoint | None:
        """Get the currently selected waypoint."""
        tree = self.query_one("#waypoint-tree", FlightPlanTree)
        if tree.cursor_node and tree.cursor_node.data:
            return tree.cursor_node.data
        return None


class FlyScreen(Screen):
    """FLY phase - waypoint implementation screen."""

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("r", "start", "Run", show=True),
        Binding("p", "pause", "Pause", show=True),
        Binding("s", "skip", "Skip", show=True),
        Binding("escape", "back", "Back", show=True),
    ]

    DEFAULT_CSS = """
    FlyScreen {
        background: $surface;
        overflow: hidden;
    }

    FlyScreen .main-container {
        width: 100%;
        height: 1fr;
    }

    FlyScreen .status-bar {
        dock: bottom;
        height: 1;
        padding: 0 2;
        background: $surface-lighten-1;
        color: $text-muted;
    }
    """

    execution_state: reactive[ExecutionState] = reactive(ExecutionState.IDLE)

    def __init__(
        self,
        project: Project,
        flight_plan: FlightPlan,
        spec: str,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan
        self.spec = spec
        self.current_waypoint: Waypoint | None = None
        self._executor: WaypointExecutor | None = None

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        with Horizontal(classes="main-container"):
            yield WaypointListPanel(id="waypoint-list")
            yield WaypointDetailPanel(id="waypoint-detail")
        yield Static(
            "Press Space to start execution", classes="status-bar", id="status-bar"
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the screen."""
        self.app.sub_title = f"{self.project.name} · Fly"

        # Update waypoint list
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        list_panel.update_flight_plan(self.flight_plan)

        # Select first pending waypoint
        self._select_next_waypoint()

        wp_count = len(self.flight_plan.waypoints)
        logger.info("FlyScreen mounted with %d waypoints", wp_count)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Update detail panel when tree selection changes."""
        if event.node.data:
            waypoint = event.node.data
            detail_panel = self.query_one(
                "#waypoint-detail", WaypointDetailPanel
            )
            detail_panel.show_waypoint(waypoint)

    def _select_next_waypoint(self) -> None:
        """Find and select the next pending waypoint."""
        for wp in self.flight_plan.waypoints:
            if wp.status == WaypointStatus.PENDING:
                # Check if dependencies are met
                def dep_complete(dep_id: str) -> bool:
                    dep_wp = self.flight_plan.get_waypoint(dep_id)
                    return (
                        dep_wp is not None
                        and dep_wp.status == WaypointStatus.COMPLETE
                    )

                deps_met = all(dep_complete(dep_id) for dep_id in wp.dependencies)
                if deps_met:
                    self.current_waypoint = wp
                    detail_panel = self.query_one(
                        "#waypoint-detail", WaypointDetailPanel
                    )
                    detail_panel.show_waypoint(wp)
                    return

        # No pending waypoints with met dependencies
        self.current_waypoint = None
        self.execution_state = ExecutionState.DONE

    def watch_execution_state(self, state: ExecutionState) -> None:
        """Update UI when execution state changes."""
        status_bar = self.query_one("#status-bar", Static)
        messages = {
            ExecutionState.IDLE: "Press Space to start execution",
            ExecutionState.RUNNING: "Executing waypoint...",
            ExecutionState.PAUSED: "Paused. Press Space to resume",
            ExecutionState.DONE: "All waypoints complete!",
            ExecutionState.INTERVENTION: "Human intervention needed",
        }
        status_bar.update(messages.get(state, ""))

    def action_start(self) -> None:
        """Start or resume waypoint execution."""
        if self.execution_state == ExecutionState.DONE:
            self.notify("All waypoints complete!")
            return

        if not self.current_waypoint:
            self._select_next_waypoint()
            if not self.current_waypoint:
                self.notify("No waypoints ready to execute")
                return

        self.execution_state = ExecutionState.RUNNING
        self._execute_current_waypoint()

    def action_pause(self) -> None:
        """Pause execution after current waypoint."""
        if self.execution_state == ExecutionState.RUNNING:
            self.execution_state = ExecutionState.PAUSED
            if self._executor:
                self._executor.cancel()
            self.notify("Will pause after current waypoint")

    def action_skip(self) -> None:
        """Skip the current waypoint."""
        if self.current_waypoint:
            wp_id = self.current_waypoint.id
            self.notify(f"Skipped {wp_id}")
            self._select_next_waypoint()

    def action_back(self) -> None:
        """Go back to CHART screen."""
        self.app.switch_phase("chart", {
            "project": self.project,
            "spec": self.spec,
        })

    def _execute_current_waypoint(self) -> None:
        """Execute the current waypoint using agentic AI."""
        if not self.current_waypoint:
            return

        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.log

        # Update status to IN_PROGRESS
        self.current_waypoint.status = WaypointStatus.IN_PROGRESS
        self._save_flight_plan()

        log.clear_log()
        wp_title = f"{self.current_waypoint.id}: {self.current_waypoint.title}"
        log.log(f"Starting {wp_title}")
        detail_panel.update_progress(5)

        # Create executor with progress callback
        self._executor = WaypointExecutor(
            project=self.project,
            waypoint=self.current_waypoint,
            spec=self.spec,
            on_progress=self._on_execution_progress,
        )

        # Run execution in background worker
        self.run_worker(
            self._run_executor(),
            name="waypoint_executor",
            exclusive=True,
        )

    async def _run_executor(self) -> ExecutionResult:
        """Run the executor asynchronously."""
        if not self._executor:
            return ExecutionResult.FAILED
        return await self._executor.execute()

    def _on_execution_progress(self, ctx: ExecutionContext) -> None:
        """Handle progress updates from the executor."""
        # Use call_from_thread to safely update UI from worker
        self.call_from_thread(self._update_progress_ui, ctx)

    def _update_progress_ui(self, ctx: ExecutionContext) -> None:
        """Update UI with progress (called on main thread)."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.log

        # Calculate progress percentage based on iteration
        max_iter = ctx.total_iterations
        progress = min(95, (ctx.iteration / max_iter) * 90 + 5)
        detail_panel.update_progress(int(progress))

        # Log based on step type
        if ctx.step == "executing":
            log.log(f"Iteration {ctx.iteration}/{max_iter}")
        elif ctx.step == "streaming":
            # Truncate long streaming output for UI
            output = ctx.output
            if len(output) > 200:
                output = output[:200] + "..."
            if output.strip():
                log.log(output)
        elif ctx.step == "complete":
            log.log_success(ctx.output)
        elif ctx.step == "error":
            log.log_error(ctx.output)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker completion."""
        if event.worker.name != "waypoint_executor":
            return

        if event.worker.is_finished:
            result = event.worker.result
            self._handle_execution_result(result)

    def _handle_execution_result(self, result: ExecutionResult | None) -> None:
        """Handle the result of waypoint execution."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.log
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)

        if result == ExecutionResult.SUCCESS:
            # Mark complete
            if self.current_waypoint:
                self.current_waypoint.status = WaypointStatus.COMPLETE
                self.current_waypoint.completed_at = datetime.now()
                self._save_flight_plan()
                log.log_success(f"Waypoint {self.current_waypoint.id} complete!")

            detail_panel.update_progress(100)
            list_panel.update_flight_plan(self.flight_plan)

            # Move to next waypoint if not paused
            if self.execution_state == ExecutionState.RUNNING:
                self._select_next_waypoint()
                if self.current_waypoint:
                    self._execute_current_waypoint()
                else:
                    self.execution_state = ExecutionState.DONE

        elif result == ExecutionResult.INTERVENTION_NEEDED:
            log.log_error("Human intervention needed")
            self.execution_state = ExecutionState.INTERVENTION
            self.notify("Waypoint needs human intervention", severity="warning")

        elif result == ExecutionResult.MAX_ITERATIONS:
            log.log_error("Max iterations reached without completion")
            self.execution_state = ExecutionState.INTERVENTION
            self.notify("Max iterations reached", severity="error")

        elif result == ExecutionResult.CANCELLED:
            log.log("Execution cancelled")
            self.execution_state = ExecutionState.PAUSED

        else:  # FAILED or None
            log.log_error("Execution failed")
            self.execution_state = ExecutionState.INTERVENTION
            self.notify("Waypoint execution failed", severity="error")

    def _save_flight_plan(self) -> None:
        """Save the flight plan to disk."""
        writer = FlightPlanWriter(self.project)
        writer.save(self.flight_plan)
