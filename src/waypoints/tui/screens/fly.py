"""FLY screen for waypoint implementation."""

import logging
import re
from datetime import datetime
from enum import Enum

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, RichLog, Static, Tree
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


# Regex patterns for markdown
CODE_BLOCK_PATTERN = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
ITALIC_PATTERN = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")


def _markdown_to_rich_text(text: str, base_style: str = "") -> Text:
    """Convert markdown formatting to Rich Text.

    Handles: **bold**, *italic*, `inline code`
    """
    result = Text()

    # Process the text character by character, tracking markdown patterns
    # This is a simplified approach - process patterns in order of precedence
    remaining = text
    while remaining:
        # Try to find the earliest markdown pattern
        bold_match = BOLD_PATTERN.search(remaining)
        italic_match = ITALIC_PATTERN.search(remaining)
        code_match = INLINE_CODE_PATTERN.search(remaining)

        # Find the earliest match
        matches = [
            (bold_match, "bold"),
            (italic_match, "italic"),
            (code_match, "code"),
        ]
        matches = [(m, t) for m, t in matches if m is not None]

        if not matches:
            # No more patterns - add remaining text
            result.append(remaining, style=base_style)
            break

        # Get earliest match
        earliest_match, match_type = min(matches, key=lambda x: x[0].start())

        # Add text before the match
        if earliest_match.start() > 0:
            result.append(remaining[: earliest_match.start()], style=base_style)

        # Add the formatted text
        inner_text = earliest_match.group(1)
        if match_type == "bold":
            style = f"{base_style} bold" if base_style else "bold"
            result.append(inner_text, style=style)
        elif match_type == "italic":
            style = f"{base_style} italic" if base_style else "italic"
            result.append(inner_text, style=style)
        elif match_type == "code":
            result.append(inner_text, style="cyan")

        # Continue with remaining text
        remaining = remaining[earliest_match.end() :]

    return result


class ExecutionLog(RichLog):
    """Rich log for execution output with syntax highlighting."""

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
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(highlight=True, markup=True, wrap=True, **kwargs)

    def log(self, message: str, level: str = "info") -> None:
        """Add a log entry with Rich formatting."""
        # Apply level-based styling
        style_map = {
            "info": "",
            "success": "green",
            "error": "red bold",
            "command": "yellow",
            "heading": "bold cyan",
        }
        style = style_map.get(level, "")

        # Process message for code blocks and markdown
        formatted = self._format_message(message, style)
        if isinstance(formatted, list):
            for item in formatted:
                self.write(item)
        else:
            self.write(formatted)

    def _format_message(
        self, message: str, default_style: str
    ) -> Text | list[Text | Syntax]:
        """Format message, extracting code blocks for syntax highlighting."""
        # Check for code blocks
        matches = list(CODE_BLOCK_PATTERN.finditer(message))
        if not matches:
            # No code blocks - convert markdown and return styled text
            return _markdown_to_rich_text(message, default_style)

        # Has code blocks - split and format
        result: list[Text | Syntax] = []
        last_end = 0

        for match in matches:
            # Add text before code block (with markdown conversion)
            if match.start() > last_end:
                before_text = message[last_end : match.start()].strip()
                if before_text:
                    result.append(
                        _markdown_to_rich_text(before_text, default_style)
                    )

            # Add syntax-highlighted code block
            lang = match.group(1) or "text"
            code = match.group(2).strip()
            result.append(
                Syntax(
                    code,
                    lang,
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True,
                )
            )
            last_end = match.end()

        # Add remaining text after last code block
        if last_end < len(message):
            after_text = message[last_end:].strip()
            if after_text:
                result.append(_markdown_to_rich_text(after_text, default_style))

        return result

    def log_command(self, command: str) -> None:
        """Log a command being executed."""
        self.write(Text(f"$ {command}", style="yellow bold"))

    def log_success(self, message: str) -> None:
        """Log a success message."""
        self.write(Text(f"✓ {message}", style="green bold"))

    def log_error(self, message: str) -> None:
        """Log an error message."""
        self.write(Text(f"✗ {message}", style="red bold"))

    def log_heading(self, message: str) -> None:
        """Log a heading/section marker."""
        self.write(Text(f"── {message} ──", style="cyan bold"))

    def clear_log(self) -> None:
        """Clear all log entries."""
        self.clear()


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

    WaypointDetailPanel .iteration-section {
        height: auto;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointDetailPanel .iteration-label {
        color: $text-muted;
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
        self._showing_output_for: str | None = None  # Track which waypoint's output

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("Select a waypoint", classes="wp-title", id="wp-title")
            yield Static("", classes="wp-objective", id="wp-objective")
            yield Static("Status: Pending", classes="wp-status", id="wp-status")
        with Vertical(classes="iteration-section"):
            yield Static("", classes="iteration-label", id="iteration-label")
        with Vertical(classes="log-section"):
            yield Static("Output", classes="log-header")
            yield ExecutionLog(id="execution-log")

    def show_waypoint(
        self,
        waypoint: Waypoint | None,
        active_waypoint_id: str | None = None,
    ) -> None:
        """Display waypoint details.

        Args:
            waypoint: The waypoint to display
            active_waypoint_id: ID of the currently executing waypoint
        """
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

            # Update output based on whether this is the active waypoint
            self._update_output_for_waypoint(waypoint, active_waypoint_id)
        else:
            title.update("Select a waypoint")
            objective.update("")
            status.update("Status: -")
            self.clear_iteration()
            self.log.clear_log()
            self._showing_output_for = None

    def _update_output_for_waypoint(
        self, waypoint: Waypoint, active_waypoint_id: str | None
    ) -> None:
        """Update the output panel based on waypoint status."""
        log = self.log

        # If this is the active waypoint, keep showing live output
        if waypoint.id == active_waypoint_id:
            # Don't clear - live output is being streamed
            self._showing_output_for = waypoint.id
            return

        # If we're already showing output for this waypoint, don't reload
        if self._showing_output_for == waypoint.id:
            return

        # Clear and show appropriate content based on status
        log.clear_log()
        self.clear_iteration()
        self._showing_output_for = waypoint.id

        if waypoint.status == WaypointStatus.COMPLETE:
            log.log_success("Waypoint completed")
            if waypoint.completed_at:
                completed = waypoint.completed_at.strftime("%Y-%m-%d %H:%M")
                log.log(f"Completed: {completed}")
        elif waypoint.status == WaypointStatus.IN_PROGRESS:
            # In progress but not active (maybe from a previous session)
            log.log("Execution in progress...")
        else:  # PENDING
            log.log("Waiting to execute")
            if waypoint.dependencies:
                deps = ", ".join(waypoint.dependencies)
                log.log(f"Dependencies: {deps}")

    def update_iteration(self, iteration: int, total: int) -> None:
        """Update the iteration display."""
        if iteration > 0:
            self.query_one("#iteration-label", Static).update(
                f"Iteration {iteration}/{total}"
            )
        else:
            self.query_one("#iteration-label", Static).update("")

    def clear_iteration(self) -> None:
        """Clear the iteration display."""
        self.query_one("#iteration-label", Static).update("")

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

    WaypointListPanel .panel-header {
        height: auto;
        padding: 1;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointListPanel .panel-title {
        text-style: bold;
        color: $text;
    }

    WaypointListPanel .progress-bar {
        color: $success;
        margin-top: 1;
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
        self._execution_state: ExecutionState = ExecutionState.IDLE
        self._blink_visible: bool = True
        self._blink_timer: object = None

    def on_mount(self) -> None:
        """Start the blink timer."""
        self._blink_timer = self.set_interval(0.5, self._toggle_blink)

    def _toggle_blink(self) -> None:
        """Toggle blink state for running indicator."""
        if self._execution_state == ExecutionState.RUNNING:
            self._blink_visible = not self._blink_visible
            self._update_overall_progress()
        elif not self._blink_visible:
            # Reset to visible when not running
            self._blink_visible = True

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("WAYPOINTS", classes="panel-title")
            yield Static(
                "□□□□□□□□□□ 0/0", classes="progress-bar", id="overall-progress"
            )
        yield FlightPlanTree(id="waypoint-tree")
        yield Static("◉ Done  ◎ Active  ○ Pending", classes="legend")

    def update_flight_plan(
        self,
        flight_plan: FlightPlan,
        execution_state: ExecutionState | None = None,
    ) -> None:
        """Update the waypoint list and overall progress."""
        self._flight_plan = flight_plan
        if execution_state is not None:
            self._execution_state = execution_state
        tree = self.query_one("#waypoint-tree", FlightPlanTree)
        tree.update_flight_plan(flight_plan)
        self._update_overall_progress()

    def update_execution_state(self, state: ExecutionState) -> None:
        """Update the execution state indicator."""
        self._execution_state = state
        self._update_overall_progress()

    def _update_overall_progress(self) -> None:
        """Update the overall progress bar with execution state."""
        if not self._flight_plan:
            return

        total = len(self._flight_plan.waypoints)
        complete = sum(
            1 for wp in self._flight_plan.waypoints
            if wp.status == WaypointStatus.COMPLETE
        )

        # Build progress bar
        if total > 0:
            percent = int((complete / total) * 100)
            filled = (complete * 10) // total if total > 0 else 0
        else:
            percent = 0
            filled = 0
        empty = 10 - filled
        bar = "■" * filled + "□" * empty

        # Build Rich Text with colored state indicator
        text = Text()
        text.append(bar, style="green")
        text.append(f" {complete}/{total} ({percent}%)", style="dim")

        # Add colored execution state indicator
        state_styles = {
            ExecutionState.IDLE: ("", ""),
            ExecutionState.RUNNING: (" ▶ Running", "bold cyan"),
            ExecutionState.PAUSED: (" ⏸ Paused", "bold yellow"),
            ExecutionState.DONE: (" ✓ Done", "bold green"),
            ExecutionState.INTERVENTION: (" ⚠ Needs Help", "bold red"),
        }
        state_text, state_style = state_styles.get(
            self._execution_state, ("", "")
        )
        if state_text:
            # Blink the play button when running
            if (
                self._execution_state == ExecutionState.RUNNING
                and not self._blink_visible
            ):
                # Show just "Running" without the play symbol
                text.append("   Running", style=state_style)
            else:
                text.append(state_text, style=state_style)

        progress_widget = self.query_one("#overall-progress", Static)
        progress_widget.update(text)

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
            active_id = (
                self.current_waypoint.id
                if self.current_waypoint
                and self.execution_state == ExecutionState.RUNNING
                else None
            )
            detail_panel.show_waypoint(waypoint, active_waypoint_id=active_id)

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
                    # No active execution yet when selecting next waypoint
                    detail_panel.show_waypoint(wp, active_waypoint_id=None)
                    return

        # No pending waypoints with met dependencies
        self.current_waypoint = None
        self.execution_state = ExecutionState.DONE

    def watch_execution_state(self, state: ExecutionState) -> None:
        """Update UI when execution state changes."""
        # Update status bar message
        status_bar = self.query_one("#status-bar", Static)
        messages = {
            ExecutionState.IDLE: "Press 'r' to start execution",
            ExecutionState.RUNNING: "Executing waypoint...",
            ExecutionState.PAUSED: "Paused. Press 'r' to resume",
            ExecutionState.DONE: "All waypoints complete!",
            ExecutionState.INTERVENTION: "Human intervention needed",
        }
        status_bar.update(messages.get(state, ""))

        # Update progress bar with execution state
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        list_panel.update_execution_state(state)

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

        # Mark this as the active waypoint for output tracking
        detail_panel._showing_output_for = self.current_waypoint.id

        log.clear_log()
        wp_title = f"{self.current_waypoint.id}: {self.current_waypoint.title}"
        log.log_heading(f"Starting {wp_title}")
        detail_panel.clear_iteration()

        # Refresh the waypoint list to show blinking status
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        list_panel.update_flight_plan(self.flight_plan)

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
        # Workers run in the same event loop, so we can update UI directly
        self._update_progress_ui(ctx)

    def _update_progress_ui(self, ctx: ExecutionContext) -> None:
        """Update UI with progress (called on main thread)."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.log

        # Update iteration display
        detail_panel.update_iteration(ctx.iteration, ctx.total_iterations)

        # Log based on step type
        if ctx.step == "executing":
            log.log_heading(f"Iteration {ctx.iteration}/{ctx.total_iterations}")
        elif ctx.step == "streaming":
            # Show streaming output (code blocks will be syntax-highlighted)
            output = ctx.output.strip()
            if output:
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

            detail_panel.clear_iteration()
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
