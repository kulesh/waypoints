"""FLY screen for waypoint implementation."""

import logging
import re
from datetime import UTC, datetime
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

from waypoints.fly.execution_log import ExecutionLogReader
from waypoints.fly.executor import (
    ExecutionContext,
    ExecutionResult,
    WaypointExecutor,
)
from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionNeededError,
    InterventionResult,
)
from waypoints.git import GitConfig, GitService, ReceiptValidator
from waypoints.models import JourneyState, Project
from waypoints.models.flight_plan import FlightPlan, FlightPlanWriter
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.screens.intervention import InterventionModal
from waypoints.tui.widgets.flight_plan import FlightPlanTree
from waypoints.tui.widgets.header import StatusHeader

logger = logging.getLogger(__name__)


class ExecutionState(Enum):
    """State of waypoint execution."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSE_PENDING = "pause_pending"  # Pause requested, finishing current waypoint
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
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $surface;
        scrollbar-background-hover: $surface;
        scrollbar-background-active: $surface;
        scrollbar-color: $surface-lighten-2;
        scrollbar-color-hover: $surface-lighten-3;
        scrollbar-color-active: $surface-lighten-3;
        scrollbar-corner-color: $surface;
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
                    result.append(_markdown_to_rich_text(before_text, default_style))

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

    def __init__(
        self, project: Project, flight_plan: FlightPlan, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)
        self._project = project
        self._flight_plan = flight_plan
        self._waypoint: Waypoint | None = None
        self._showing_output_for: str | None = None  # Track which waypoint's output
        self._is_live_output: bool = False  # True if showing live streaming output

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("Select a waypoint", classes="wp-title", id="wp-title")
            yield Static("", classes="wp-objective", id="wp-objective")
            yield Static("Status: Pending", classes="wp-status", id="wp-status")
        with Vertical(classes="iteration-section"):
            yield Static("", classes="iteration-label", id="iteration-label")
        with Vertical(classes="log-section"):
            yield Static("Output", classes="log-header", id="log-header")
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
        log_header = self.query_one("#log-header", Static)

        if waypoint:
            title.update(f"{waypoint.id}: {waypoint.title}")
            obj_text = waypoint.objective
            if len(obj_text) > 100:
                obj_text = obj_text[:97] + "..."
            objective.update(obj_text)
            status_text = waypoint.status.value.replace("_", " ").title()
            status.update(f"Status: {status_text}")

            # Update log header with waypoint ID
            log_header.update(f"Output · {waypoint.id}")

            # Update output based on whether this is the active waypoint
            self._update_output_for_waypoint(waypoint, active_waypoint_id)
        else:
            title.update("Select a waypoint")
            objective.update("")
            status.update("Status: -")
            log_header.update("Output")
            self.clear_iteration()
            self.log.clear_log()
            self._showing_output_for = None
            self._is_live_output = False

    def _update_output_for_waypoint(
        self, waypoint: Waypoint, active_waypoint_id: str | None
    ) -> None:
        """Update the output panel based on waypoint status."""
        log = self.log

        # If this is the active waypoint, keep showing live output
        if waypoint.id == active_waypoint_id:
            # Don't clear - live output is being streamed
            self._showing_output_for = waypoint.id
            self._is_live_output = True
            return

        # If we're showing live output for a DIFFERENT waypoint, don't switch away
        # (user navigated to view a different waypoint while execution continues)
        if self._is_live_output and self._showing_output_for == active_waypoint_id:
            # We're showing live output for the active waypoint, but user selected
            # a different waypoint. Clear and show the selected waypoint's history.
            pass  # Fall through to show the selected waypoint

        # If we're already showing historical output for this waypoint, don't reload
        if self._showing_output_for == waypoint.id and not self._is_live_output:
            return

        # Clear and show appropriate content based on status
        log.clear_log()
        self.clear_iteration()
        self._showing_output_for = waypoint.id
        self._is_live_output = False

        # Check if this is an epic (multi-hop waypoint with children)
        if self._flight_plan.is_epic(waypoint.id):
            self._show_epic_details(waypoint)
            return

        # Try to load historical execution log for completed/failed waypoints
        if waypoint.status in (WaypointStatus.COMPLETE, WaypointStatus.FAILED):
            if self._load_execution_history(waypoint):
                return  # Successfully loaded history

        # Fallback to status-based messages
        if waypoint.status == WaypointStatus.COMPLETE:
            log.log_success("Waypoint completed")
            if waypoint.completed_at:
                completed = waypoint.completed_at.strftime("%Y-%m-%d %H:%M")
                log.log(f"Completed: {completed}")
            log.log("(No execution log found)")
        elif waypoint.status == WaypointStatus.FAILED:
            log.log_error("Last execution failed")
            log.log("Press 'r' to retry")
            log.log("(No execution log found)")
        elif waypoint.status == WaypointStatus.IN_PROGRESS:
            # In progress but not active (stale from previous session)
            log.log("Execution was in progress...")
            log.log("(Session may have been interrupted)")
        else:  # PENDING
            log.log("Waiting to execute")
            if waypoint.dependencies:
                deps = ", ".join(waypoint.dependencies)
                log.log(f"Dependencies: {deps}")

    def _load_execution_history(self, waypoint: Waypoint) -> bool:
        """Load and display execution history from disk.

        Args:
            waypoint: The waypoint to load history for

        Returns:
            True if history was loaded, False otherwise
        """
        try:
            exec_log = ExecutionLogReader.load_latest(
                self._project, waypoint_id=waypoint.id
            )
            if not exec_log:
                return False

            log = self.log

            # Show execution summary header
            log.log_heading(f"Execution Log · {exec_log.execution_id[:8]}")
            started = exec_log.started_at.strftime("%Y-%m-%d %H:%M")
            log.log(f"Started: {started}")

            if exec_log.completed_at:
                completed = exec_log.completed_at.strftime("%Y-%m-%d %H:%M")
                duration = (exec_log.completed_at - exec_log.started_at).seconds
                log.log(f"Completed: {completed} ({duration}s)")

            if exec_log.result:
                if exec_log.result == "success":
                    log.log_success(f"Result: {exec_log.result}")
                else:
                    log.log_error(f"Result: {exec_log.result}")

            if exec_log.total_cost_usd > 0:
                log.log(f"Cost: ${exec_log.total_cost_usd:.4f}")

            log.log("")  # Blank line

            # Show execution entries (summarized)
            for entry in exec_log.entries:
                if entry.entry_type == "iteration_start":
                    log.log_heading(f"Iteration {entry.iteration}")
                elif entry.entry_type == "output":
                    # Show output content (may contain code blocks)
                    if entry.content:
                        # Truncate very long outputs
                        content = entry.content
                        if len(content) > 2000:
                            content = content[:2000] + "\n... (truncated)"
                        log.log(content)
                elif entry.entry_type == "error":
                    log.log_error(entry.content)
                elif entry.entry_type == "iteration_end":
                    cost = entry.metadata.get("cost_usd")
                    if cost:
                        log.log(f"(Iteration cost: ${cost:.4f})")

            return True

        except Exception as e:
            logger.warning(
                "Failed to load execution history for %s: %s", waypoint.id, e
            )
            return False

    def _show_epic_details(self, waypoint: Waypoint) -> None:
        """Display details for an epic (multi-hop waypoint with children).

        Shows the epic's children and their status instead of execution output.

        Args:
            waypoint: The epic waypoint to display
        """
        log = self.log
        children = self._flight_plan.get_children(waypoint.id)

        log.log_heading("Multi-hop Waypoint")
        log.log(f"This waypoint contains {len(children)} child tasks.")
        log.log("")

        # Calculate progress
        complete = sum(1 for c in children if c.status == WaypointStatus.COMPLETE)
        failed = sum(1 for c in children if c.status == WaypointStatus.FAILED)
        in_progress = sum(1 for c in children if c.status == WaypointStatus.IN_PROGRESS)

        # Progress summary
        if complete == len(children):
            log.log_success(f"Progress: {complete}/{len(children)} complete")
        elif failed > 0:
            log.log(f"Progress: {complete}/{len(children)} complete, {failed} failed")
        elif in_progress > 0:
            log.log(f"Progress: {complete}/{len(children)} complete, 1 in progress")
        else:
            log.log(f"Progress: {complete}/{len(children)} complete")

        log.log("")
        log.log("Children:")

        # Status icons
        status_icons = {
            WaypointStatus.COMPLETE: ("◉", "green"),
            WaypointStatus.IN_PROGRESS: ("◎", "cyan"),
            WaypointStatus.FAILED: ("✗", "red"),
            WaypointStatus.PENDING: ("○", "dim"),
        }

        # Show each child
        for child in children:
            icon, style = status_icons.get(child.status, ("○", "dim"))
            status_label = child.status.value.replace("_", " ").lower()
            text = Text()
            text.append(f"  {icon} ", style=style)
            text.append(f"{child.id}: {child.title}")
            text.append(f" ({status_label})", style="dim")
            log.write(text)

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
        # Blink for both RUNNING and PAUSE_PENDING (still executing)
        if self._execution_state in (
            ExecutionState.RUNNING,
            ExecutionState.PAUSE_PENDING,
        ):
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
        yield Static("◉ Done  ◎ Active  ✗ Failed  ○ Pending", classes="legend")

    def update_flight_plan(
        self,
        flight_plan: FlightPlan,
        execution_state: ExecutionState | None = None,
        cost_by_waypoint: dict[str, float] | None = None,
    ) -> None:
        """Update the waypoint list and overall progress.

        Args:
            flight_plan: The flight plan to display.
            execution_state: Optional new execution state.
            cost_by_waypoint: Optional dict mapping waypoint ID to cost in USD.
        """
        self._flight_plan = flight_plan
        if execution_state is not None:
            self._execution_state = execution_state
        tree = self.query_one("#waypoint-tree", FlightPlanTree)
        tree.update_flight_plan(flight_plan, cost_by_waypoint)
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
            1
            for wp in self._flight_plan.waypoints
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
            ExecutionState.PAUSE_PENDING: (" ⏸ Pausing...", "bold yellow"),
            ExecutionState.PAUSED: (" ⏸ Paused", "bold yellow"),
            ExecutionState.DONE: (" ✓ Done", "bold green"),
            ExecutionState.INTERVENTION: (" ⚠ Needs Help", "bold red"),
        }
        state_text, state_style = state_styles.get(self._execution_state, ("", ""))
        if state_text:
            # Blink the icon when running or pause pending
            if (
                self._execution_state
                in (
                    ExecutionState.RUNNING,
                    ExecutionState.PAUSE_PENDING,
                )
                and not self._blink_visible
            ):
                # Show text without the icon symbol
                if self._execution_state == ExecutionState.RUNNING:
                    text.append("   Running", style=state_style)
                else:
                    text.append("   Pausing...", style=state_style)
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
        self._current_intervention: Intervention | None = None
        self._additional_iterations: int = 0
        # Timer tracking
        self._execution_start: datetime | None = None
        self._elapsed_before_pause: float = 0.0
        self._ticker_timer: object | None = None

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        with Horizontal(classes="main-container"):
            yield WaypointListPanel(id="waypoint-list")
            yield WaypointDetailPanel(
                project=self.project,
                flight_plan=self.flight_plan,
                id="waypoint-detail",
            )
        yield Static(
            "Press Space to start execution", classes="status-bar", id="status-bar"
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the screen."""
        self.app.sub_title = f"{self.project.name} · Fly"

        # Set up metrics collection for this project
        self.app.set_project_for_metrics(self.project)

        # Clean up stale IN_PROGRESS from previous sessions
        self._reset_stale_in_progress()

        # Update waypoint list with cost data
        self._refresh_waypoint_list()

        # Select first pending waypoint
        self._select_next_waypoint()

        # Update status bar with initial state (watcher doesn't fire on mount)
        self._update_status_bar(self.execution_state)

        wp_count = len(self.flight_plan.waypoints)
        logger.info("FlyScreen mounted with %d waypoints", wp_count)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Update detail panel when tree selection changes."""
        if event.node.data:
            waypoint = event.node.data
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            active_id = (
                self.current_waypoint.id
                if self.current_waypoint
                and self.execution_state == ExecutionState.RUNNING
                else None
            )
            detail_panel.show_waypoint(waypoint, active_waypoint_id=active_id)

    def _select_next_waypoint(self, include_in_progress: bool = False) -> None:
        """Find and select the next waypoint to execute.

        Args:
            include_in_progress: If True, also consider IN_PROGRESS and FAILED
                                waypoints (for resume after pause/failure)
        """
        logger.debug(
            "=== Selection round (include_in_progress=%s) ===", include_in_progress
        )

        # If resuming, first check for IN_PROGRESS or FAILED waypoints
        if include_in_progress:
            for wp in self.flight_plan.waypoints:
                if wp.status in (WaypointStatus.IN_PROGRESS, WaypointStatus.FAILED):
                    # Skip epics - only execute leaf waypoints
                    if self.flight_plan.is_epic(wp.id):
                        logger.debug("SKIP %s: is epic", wp.id)
                        continue
                    logger.info(
                        "SELECTED %s: resuming %s waypoint", wp.id, wp.status.value
                    )
                    self.current_waypoint = wp
                    detail_panel = self.query_one(
                        "#waypoint-detail", WaypointDetailPanel
                    )
                    detail_panel.show_waypoint(wp, active_waypoint_id=None)
                    return

        # Then check for PENDING waypoints with met dependencies
        for wp in self.flight_plan.waypoints:
            if wp.status != WaypointStatus.PENDING:
                logger.debug("SKIP %s: status=%s", wp.id, wp.status.value)
                continue

            # Skip epics (multi-hop waypoints) - only execute leaf waypoints
            # Epics are auto-completed when all children are complete
            if self.flight_plan.is_epic(wp.id):
                logger.debug("SKIP %s: is epic", wp.id)
                continue

            # Check if dependencies are met (COMPLETE or SKIPPED)
            unmet_deps = []
            for dep_id in wp.dependencies:
                dep_wp = self.flight_plan.get_waypoint(dep_id)
                if dep_wp is None:
                    unmet_deps.append(f"{dep_id}(not found)")
                elif dep_wp.status not in (
                    WaypointStatus.COMPLETE,
                    WaypointStatus.SKIPPED,
                ):
                    unmet_deps.append(f"{dep_id}({dep_wp.status.value})")

            if unmet_deps:
                logger.debug("SKIP %s: blocked by %s", wp.id, ", ".join(unmet_deps))
                continue

            logger.info("SELECTED %s: all deps satisfied", wp.id)
            self.current_waypoint = wp
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            detail_panel.show_waypoint(wp, active_waypoint_id=None)
            return

        # No waypoints found
        logger.info("No eligible waypoints found - DONE")
        self.current_waypoint = None
        self.execution_state = ExecutionState.DONE

    def _get_state_message(self, state: ExecutionState) -> str:
        """Get the status bar message for a given execution state."""
        if state == ExecutionState.IDLE:
            if self.current_waypoint:
                wp = self.current_waypoint
                # Truncate title if too long
                title = wp.title[:40] + "..." if len(wp.title) > 40 else wp.title
                return f"Press 'r' to run {wp.id}: {title}"
            return "No waypoints ready to run"
        elif state == ExecutionState.RUNNING:
            return "Executing waypoint..."
        elif state == ExecutionState.PAUSE_PENDING:
            return "Pausing after current waypoint..."
        elif state == ExecutionState.PAUSED:
            if self.current_waypoint:
                return f"Paused. Press 'r' to run {self.current_waypoint.id}"
            return "Paused. Press 'r' to continue"
        elif state == ExecutionState.DONE:
            return "All waypoints complete!"
        elif state == ExecutionState.INTERVENTION:
            if self.current_waypoint:
                return f"Intervention needed for {self.current_waypoint.id}"
            return "Intervention needed"
        return ""

    def _update_ticker(self) -> None:
        """Update the status bar with elapsed time and cost."""
        if self._execution_start is None:
            return

        current_elapsed = (datetime.now(UTC) - self._execution_start).total_seconds()
        total_elapsed = self._elapsed_before_pause + current_elapsed
        minutes, seconds = divmod(int(total_elapsed), 60)

        cost = (
            self.app.metrics_collector.total_cost if self.app.metrics_collector else 0.0
        )

        status_bar = self.query_one("#status-bar", Static)
        message = self._get_state_message(self.execution_state)
        status_bar.update(f"⏱ {minutes}:{seconds:02d} | ${cost:.2f}    {message}")

    def _update_status_bar(self, state: ExecutionState) -> None:
        """Update the status bar with state message and optional cost."""
        status_bar = self.query_one("#status-bar", Static)
        message = self._get_state_message(state)

        if state == ExecutionState.RUNNING and self._execution_start:
            # Timer callback will handle updates
            return

        # Show cost even when not running (if there's any)
        cost = (
            self.app.metrics_collector.total_cost if self.app.metrics_collector else 0.0
        )
        if cost > 0:
            status_bar.update(f"${cost:.2f}    {message}")
        else:
            status_bar.update(message)

    def watch_execution_state(self, state: ExecutionState) -> None:
        """Update UI when execution state changes."""
        # Handle timer based on state transitions
        if state == ExecutionState.RUNNING:
            # Start or resume timer
            self._execution_start = datetime.now(UTC)
            self._ticker_timer = self.set_interval(1.0, self._update_ticker)
        elif state == ExecutionState.PAUSED:
            # Accumulate elapsed time before stopping
            if self._execution_start:
                elapsed = (datetime.now(UTC) - self._execution_start).total_seconds()
                self._elapsed_before_pause += elapsed
            if self._ticker_timer:
                self._ticker_timer.stop()
                self._ticker_timer = None
        elif state in (ExecutionState.DONE, ExecutionState.IDLE):
            # Reset everything
            if self._ticker_timer:
                self._ticker_timer.stop()
                self._ticker_timer = None
            self._elapsed_before_pause = 0.0
            self._execution_start = None

        # Update status bar
        self._update_status_bar(state)

        # Update progress bar with execution state
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        list_panel.update_execution_state(state)

    def action_start(self) -> None:
        """Start or resume waypoint execution."""
        if self.execution_state == ExecutionState.DONE:
            self.notify("All waypoints complete!")
            return

        # Handle resume from paused state
        if self.execution_state == ExecutionState.PAUSED:
            # Find waypoint to resume (in_progress first, then pending)
            self._select_next_waypoint(include_in_progress=True)
            if not self.current_waypoint:
                self.notify("No waypoints to resume")
                return
            # Transition journey state: FLY_PAUSED -> FLY_EXECUTING
            self.project.transition_journey(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            self._execute_current_waypoint()
            return

        if not self.current_waypoint:
            self._select_next_waypoint()
            if not self.current_waypoint:
                self.notify("No waypoints ready to execute")
                return

        # Transition journey state to FLY_EXECUTING
        # Handle case where we came from Chart via Ctrl+F (state may be CHART_REVIEW)
        journey = self.project.journey
        if journey and journey.state == JourneyState.CHART_REVIEW:
            self.project.transition_journey(JourneyState.FLY_READY)
        self.project.transition_journey(JourneyState.FLY_EXECUTING)
        self.execution_state = ExecutionState.RUNNING
        self._execute_current_waypoint()

    def action_pause(self) -> None:
        """Pause execution after current waypoint."""
        if self.execution_state == ExecutionState.RUNNING:
            self.execution_state = ExecutionState.PAUSE_PENDING
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
        # Transition journey state back to CHART_REVIEW if in FLY_READY or intervention
        if self.project.journey and self.project.journey.state in (
            JourneyState.FLY_READY,
            JourneyState.FLY_INTERVENTION,
            JourneyState.FLY_PAUSED,
        ):
            self.project.transition_journey(JourneyState.CHART_REVIEW)

        # Load spec and brief from disk to ensure we have content
        spec = self.app._load_latest_doc(self.project, "product-spec")  # type: ignore[attr-defined]
        brief = self.app._load_latest_doc(self.project, "idea-brief")  # type: ignore[attr-defined]
        self.app.switch_phase(
            "chart",
            {
                "project": self.project,
                "spec": spec or self.spec,
                "brief": brief,
            },
        )

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
        self._refresh_waypoint_list()

        # Calculate max iterations (default + any additional from retry)
        from waypoints.fly.executor import MAX_ITERATIONS

        max_iters = MAX_ITERATIONS + self._additional_iterations
        self._additional_iterations = 0  # Reset for next execution

        # Create executor with progress callback
        self._executor = WaypointExecutor(
            project=self.project,
            waypoint=self.current_waypoint,
            spec=self.spec,
            on_progress=self._on_execution_progress,
            max_iterations=max_iters,
            metrics_collector=self.app.metrics_collector,
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
            # Check for InterventionNeededError exception
            if event.worker.state.name == "ERROR":
                # Worker raised an exception - check if it's an intervention
                try:
                    # Accessing result will re-raise the exception
                    _ = event.worker.result
                except InterventionNeededError as e:
                    self._handle_intervention(e.intervention)
                    return
                except Exception as e:
                    # Other exception - treat as failure
                    logger.exception("Worker failed with exception: %s", e)
                    self._handle_execution_result(ExecutionResult.FAILED)
                    return

            result = event.worker.result
            self._handle_execution_result(result)

    def _handle_execution_result(self, result: ExecutionResult | None) -> None:
        """Handle the result of waypoint execution."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.log

        # Update header cost display after execution
        self.app.update_header_cost()

        if result == ExecutionResult.SUCCESS:
            # Mark complete
            if self.current_waypoint:
                self.current_waypoint.status = WaypointStatus.COMPLETE
                self.current_waypoint.completed_at = datetime.now()
                log.log_success(f"Waypoint {self.current_waypoint.id} complete!")

                # Check if parent epic should be auto-completed
                self._check_parent_completion(self.current_waypoint)

                self._save_flight_plan()

                # Commit waypoint completion (validates receipt first)
                self._commit_waypoint(self.current_waypoint)

            detail_panel.clear_iteration()
            self._refresh_waypoint_list()

            # Move to next waypoint if not paused/pausing
            if self.execution_state == ExecutionState.RUNNING:
                self._select_next_waypoint()
                if self.current_waypoint:
                    self._execute_current_waypoint()
                else:
                    # Transition journey state: FLY_EXECUTING -> LANDED
                    self.project.transition_journey(JourneyState.LANDED)
                    self.execution_state = ExecutionState.DONE
            elif self.execution_state == ExecutionState.PAUSE_PENDING:
                # Pause was requested, now actually pause
                # Transition journey state: FLY_EXECUTING -> FLY_PAUSED
                self.project.transition_journey(JourneyState.FLY_PAUSED)
                self.execution_state = ExecutionState.PAUSED

        elif result == ExecutionResult.INTERVENTION_NEEDED:
            log.log_error("Human intervention needed")
            self._mark_waypoint_failed()
            # Transition journey state: FLY_EXECUTING -> FLY_INTERVENTION
            self.project.transition_journey(JourneyState.FLY_INTERVENTION)
            self.execution_state = ExecutionState.INTERVENTION
            self.query_one(StatusHeader).set_error()
            self.notify("Waypoint needs human intervention", severity="warning")

        elif result == ExecutionResult.MAX_ITERATIONS:
            log.log_error("Max iterations reached without completion")
            self._mark_waypoint_failed()
            # Transition journey state: FLY_EXECUTING -> FLY_INTERVENTION
            self.project.transition_journey(JourneyState.FLY_INTERVENTION)
            self.execution_state = ExecutionState.INTERVENTION
            self.query_one(StatusHeader).set_error()
            self.notify("Max iterations reached", severity="error")

        elif result == ExecutionResult.CANCELLED:
            log.log("Execution cancelled")
            # Transition journey state: FLY_EXECUTING -> FLY_PAUSED
            self.project.transition_journey(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED

        else:  # FAILED or None
            log.log_error("Execution failed")
            self._mark_waypoint_failed()
            # Transition journey state: FLY_EXECUTING -> FLY_INTERVENTION
            self.project.transition_journey(JourneyState.FLY_INTERVENTION)
            self.execution_state = ExecutionState.INTERVENTION
            self.query_one(StatusHeader).set_error()
            self.notify("Waypoint execution failed", severity="error")

    def _handle_intervention(self, intervention: Intervention) -> None:
        """Handle an intervention request by showing the modal."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.log

        # Log the intervention
        type_label = intervention.type.value.replace("_", " ").title()
        log.log_error(f"Intervention needed: {type_label}")
        log.log(intervention.error_summary[:500])

        # Store the intervention for retry handling
        self._current_intervention = intervention

        # Mark waypoint as failed (can be retried)
        self._mark_waypoint_failed()

        # Transition journey state: FLY_EXECUTING -> FLY_INTERVENTION
        self.project.transition_journey(JourneyState.FLY_INTERVENTION)
        self.execution_state = ExecutionState.INTERVENTION
        self.query_one(StatusHeader).set_error()

        # Show the intervention modal
        self.push_screen(
            InterventionModal(intervention),
            callback=self._on_intervention_result,
        )

    def _on_intervention_result(self, result: InterventionResult | None) -> None:
        """Handle the result of the intervention modal."""
        if result is None:
            # User cancelled - treat as abort
            self.notify("Intervention cancelled")
            return

        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.log

        if result.action == InterventionAction.RETRY:
            # Retry with additional iterations
            log.log(
                f"Retrying with {result.additional_iterations} additional iterations"
            )
            self._additional_iterations = result.additional_iterations

            # Reset waypoint status for retry
            if self.current_waypoint:
                self.current_waypoint.status = WaypointStatus.IN_PROGRESS
                self._save_flight_plan()
                self._refresh_waypoint_list()

            # Transition: FLY_INTERVENTION -> FLY_EXECUTING
            self.project.transition_journey(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            self.query_one(StatusHeader).set_normal()
            self._execute_current_waypoint()

        elif result.action == InterventionAction.SKIP:
            # Skip this waypoint and move to next
            log.log("Skipping waypoint")
            if self.current_waypoint:
                self.current_waypoint.status = WaypointStatus.SKIPPED
                self._save_flight_plan()
                self._refresh_waypoint_list()

            # Transition: FLY_INTERVENTION -> FLY_PAUSED -> FLY_EXECUTING
            self.project.transition_journey(JourneyState.FLY_PAUSED)
            self.project.transition_journey(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            self.query_one(StatusHeader).set_normal()
            self._select_next_waypoint()
            if self.current_waypoint:
                self._execute_current_waypoint()
            else:
                self.project.transition_journey(JourneyState.LANDED)
                self.execution_state = ExecutionState.DONE
                self.notify("All waypoints complete!")

        elif result.action == InterventionAction.EDIT:
            # Open waypoint editor - for now, just notify
            log.log("Edit waypoint requested")
            self.notify(
                "Edit waypoint in flight plan, then press 'r' to retry",
                severity="information",
            )
            # Stay in intervention state until user edits and retries
            # Transition: FLY_INTERVENTION -> FLY_PAUSED
            self.project.transition_journey(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self.query_one(StatusHeader).set_normal()

        elif result.action == InterventionAction.ROLLBACK:
            # Rollback to last safe tag
            log.log("Rolling back to last safe tag")
            self._rollback_to_safe_tag(result.rollback_tag)
            # Transition: FLY_INTERVENTION -> FLY_READY
            self.project.transition_journey(JourneyState.FLY_PAUSED)
            self.project.transition_journey(JourneyState.FLY_READY)
            self.execution_state = ExecutionState.IDLE
            self.query_one(StatusHeader).set_normal()

        elif result.action == InterventionAction.ABORT:
            # Abort execution
            log.log("Execution aborted")
            # Transition: FLY_INTERVENTION -> FLY_PAUSED
            self.project.transition_journey(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self.query_one(StatusHeader).set_normal()
            self.notify("Execution aborted")

        # Clear the current intervention
        self._current_intervention = None

    def _rollback_to_safe_tag(self, tag: str | None) -> None:
        """Rollback git to the specified tag or find the last safe one."""
        git = GitService()

        if not git.is_git_repo():
            self.notify("Not a git repository - cannot rollback", severity="error")
            return

        if tag:
            # Use specified tag
            target_tag = tag
        else:
            # Find last safe tag (project/WP-* pattern)
            # This is a simplified version - a full implementation would list tags
            self.notify(
                "No rollback tag specified - please use git manually",
                severity="warning",
            )
            return

        # Perform the rollback
        # TODO: GitService.run_command doesn't exist - implement when rollback is needed
        result = git.run_command(["reset", "--hard", target_tag])  # type: ignore[attr-defined]
        if result.success:
            self.notify(f"Rolled back to {target_tag}")
            # Reload flight plan to reflect any changes
            # TODO: Implement flight plan reload
        else:
            self.notify(f"Rollback failed: {result.message}", severity="error")

    def _mark_waypoint_failed(self) -> None:
        """Mark the current waypoint as failed and save."""
        if self.current_waypoint:
            self.current_waypoint.status = WaypointStatus.FAILED
            self._save_flight_plan()
            # Update the tree display
            self._refresh_waypoint_list()

    def _refresh_waypoint_list(
        self, execution_state: ExecutionState | None = None
    ) -> None:
        """Refresh the waypoint list with current cost data.

        Args:
            execution_state: Optional execution state to update.
        """
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        cost_by_waypoint = None
        if self.app.metrics_collector:
            cost_by_waypoint = self.app.metrics_collector.cost_by_waypoint()
        list_panel.update_flight_plan(
            self.flight_plan, execution_state, cost_by_waypoint
        )

    def _save_flight_plan(self) -> None:
        """Save the flight plan to disk."""
        writer = FlightPlanWriter(self.project)
        writer.save(self.flight_plan)

    def _commit_waypoint(self, waypoint: Waypoint) -> None:
        """Commit waypoint completion if receipt is valid.

        Implements the "trust but verify" pattern:
        - Model already produced receipt during execution
        - We validate receipt exists and is well-formed
        - If valid, commit the changes
        - If invalid, skip commit but don't block
        """
        config = GitConfig.load()

        if not config.auto_commit:
            logger.debug("Auto-commit disabled, skipping")
            return

        git = GitService()

        # Auto-init if needed
        if not git.is_git_repo():
            if config.auto_init:
                init_result = git.init_repo()
                if init_result.success:
                    self.notify("Initialized git repository")
                else:
                    logger.warning("Failed to init git repo: %s", init_result.message)
                    return
            else:
                logger.debug("Not a git repo and auto-init disabled")
                return

        # Validate receipt (the "dog" checking the "pilot's" work)
        if config.run_checklist:
            validator = ReceiptValidator()
            receipt_path = validator.find_latest_receipt(
                self.project.get_path(), waypoint.id
            )

            if receipt_path:
                validation_result = validator.validate(receipt_path)
                if not validation_result.valid:
                    logger.warning(
                        "Skipping commit - receipt invalid: %s",
                        validation_result.message,
                    )
                    self.notify(
                        f"Skipping commit: {validation_result.message}",
                        severity="warning",
                    )
                    return
                logger.info("Receipt validated: %s", receipt_path)
            else:
                logger.warning("Skipping commit - no receipt found for %s", waypoint.id)
                self.notify(
                    f"Skipping commit: no receipt for {waypoint.id}", severity="warning"
                )
                return

        # Stage project files and commit
        git.stage_project_files(self.project.slug)

        # Build commit message
        commit_msg = f"feat({self.project.slug}): Complete {waypoint.title}"
        result = git.commit(commit_msg)

        if result.success:
            if "Nothing to commit" not in result.message:
                logger.info("Committed: %s", commit_msg)
                self.notify(f"Committed: {waypoint.id}")

                # Create tag for waypoint if configured
                if config.create_waypoint_tags:
                    tag_name = f"{self.project.slug}/{waypoint.id}"
                    git.tag(tag_name, f"Completed waypoint: {waypoint.title}")
        else:
            logger.error("Commit failed: %s", result.message)
            self.notify(f"Commit failed: {result.message}", severity="error")

    def _check_parent_completion(self, completed_waypoint: Waypoint) -> None:
        """Check if parent epic should be auto-completed.

        When a child waypoint completes, check if all siblings are also complete.
        If so, mark the parent epic as complete. This cascades up the tree.

        Args:
            completed_waypoint: The waypoint that just completed
        """
        if not completed_waypoint.parent_id:
            return  # No parent, nothing to check

        parent = self.flight_plan.get_waypoint(completed_waypoint.parent_id)
        if not parent:
            return

        # Get all children of the parent
        children = self.flight_plan.get_children(parent.id)
        if not children:
            return

        # Check if ALL children are complete
        all_complete = all(
            child.status == WaypointStatus.COMPLETE for child in children
        )

        if all_complete:
            # Mark parent as complete
            parent.status = WaypointStatus.COMPLETE
            parent.completed_at = datetime.now()
            logger.info(
                "Auto-completed parent epic %s (all %d children complete)",
                parent.id,
                len(children),
            )

            # Recursively check grandparent
            self._check_parent_completion(parent)

    def _reset_stale_in_progress(self) -> None:
        """Reset any stale IN_PROGRESS waypoints to PENDING.

        Called on session start to clean up state from crashed/killed sessions.
        Only one waypoint should be IN_PROGRESS at a time, and only during
        active execution in the current session.
        """
        changed = False
        for wp in self.flight_plan.waypoints:
            if wp.status == WaypointStatus.IN_PROGRESS:
                wp.status = WaypointStatus.PENDING
                changed = True
                logger.info("Reset stale IN_PROGRESS waypoint %s to PENDING", wp.id)
        if changed:
            self._save_flight_plan()
