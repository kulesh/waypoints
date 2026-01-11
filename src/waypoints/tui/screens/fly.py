"""FLY screen for waypoint implementation."""

import logging
import re
import subprocess
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, RichLog, Static, Tree
from textual.worker import Worker

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.fly.execution_log import (
    ExecutionLog as ExecLogType,
    ExecutionLogReader,
)
from waypoints.fly.executor import (
    ExecutionContext,
    ExecutionResult,
    FileOperation,
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
from waypoints.orchestration import JourneyCoordinator
from waypoints.tui.screens.intervention import InterventionModal
from waypoints.tui.utils import (
    get_status_color,
    get_status_icon,
    get_status_label,
    get_status_markup,
)
from waypoints.tui.widgets.file_preview import FilePreviewModal
from waypoints.tui.widgets.flight_plan import FlightPlanTree
from waypoints.tui.widgets.header import StatusHeader
from waypoints.tui.widgets.resizable_split import ResizableSplit

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


def get_git_status_summary(project_path: Path) -> str:
    """Get git status with colored indicator: 'branch [color]●[/] N changed'."""
    try:
        # Get current branch
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if branch_result.returncode != 0:
            return ""  # Not a git repo
        branch = branch_result.stdout.strip() or "HEAD"

        # Get status (use -uall to show individual files in untracked directories)
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [line for line in status_result.stdout.strip().split("\n") if line]

        if not lines:
            return f"{branch} [green]✓[/]"

        # Count untracked (??) vs modified
        untracked = sum(1 for line in lines if line.startswith("??"))

        if untracked > 0:
            # Red: has untracked files
            return f"{branch} [red]●[/] {len(lines)} changed"
        else:
            # Yellow: modified only
            return f"{branch} [yellow]●[/] {len(lines)} changed"
    except Exception:
        return ""


def _markdown_to_rich_text(text: str, base_style: str = "") -> Text:
    """Convert markdown and Rich markup formatting to Rich Text.

    Handles:
    - Rich markup: [green]text[/], [bold]text[/], etc.
    - Markdown: **bold**, *italic*, `inline code`
    """
    # Check if text contains Rich markup tags - use Rich's built-in processing
    if "[" in text and "[/" in text:
        result = Text.from_markup(text)
        if base_style:
            result.stylize(base_style)
        return result

    # Otherwise, process markdown patterns
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
        matches_with_none: list[tuple[re.Match[str] | None, str]] = [
            (bold_match, "bold"),
            (italic_match, "italic"),
            (code_match, "code"),
        ]
        matches: list[tuple[re.Match[str], str]] = [
            (m, t) for m, t in matches_with_none if m is not None
        ]

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
        link-color: cyan;
        link-style: underline;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(highlight=True, markup=True, wrap=True, **kwargs)

    def write_log(self, message: str, level: str = "info") -> None:
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


class AcceptanceCriteriaList(Static):
    """Displays acceptance criteria with live checkboxes."""

    DEFAULT_CSS = """
    AcceptanceCriteriaList {
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._criteria: list[str] = []
        self._completed: set[int] = set()

    def set_criteria(
        self, criteria: list[str], completed: set[int] | None = None
    ) -> None:
        """Set the list of criteria to display with optional initial completed state."""
        self._criteria = criteria
        self._completed = completed if completed is not None else set()
        self._refresh_display()

    def update_completed(self, completed: set[int]) -> None:
        """Update which criteria are marked complete."""
        if completed != self._completed:
            self._completed = completed
            self._refresh_display()

    def _refresh_display(self) -> None:
        """Render the criteria list with checkboxes."""
        if not self._criteria:
            self.update("")
            return

        lines = []
        for i, criterion in enumerate(self._criteria):
            if i in self._completed:
                lines.append(f"[green]\\[✓][/] {criterion}")
            else:
                lines.append(f"[dim]\\[ ][/] {criterion}")
        self.update("\n".join(lines))


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
        padding: 1 1 1 0;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointDetailPanel .wp-title {
        text-style: bold;
    }

    WaypointDetailPanel .wp-objective {
        color: $text-muted;
    }

    WaypointDetailPanel .wp-status {
        color: $text-muted;
    }

    WaypointDetailPanel .criteria-section {
        height: auto;
        padding: 1 1 1 0;
        border-bottom: solid $surface-lighten-1;
    }

    WaypointDetailPanel .section-label {
        color: $text-muted;
    }

    WaypointDetailPanel .log-section {
        height: 1fr;
    }
    """

    def __init__(
        self, project: Project, flight_plan: FlightPlan, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._project = project
        self._flight_plan = flight_plan
        self._waypoint: Waypoint | None = None
        self._waypoint_cost: float | None = None
        self._showing_output_for: str | None = None  # Track which waypoint's output
        self._is_live_output: bool = False  # True if showing live streaming output

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("Select a waypoint", classes="wp-title", id="wp-title")
            yield Static("", classes="wp-objective", id="wp-objective")
            yield Static("Status: Pending", classes="wp-status", id="wp-status")
            yield Static("", classes="wp-status", id="iteration-label")
        with Vertical(classes="criteria-section", id="criteria-section"):
            yield Static("Acceptance Criteria", classes="section-label")
            yield AcceptanceCriteriaList(id="criteria-list")
        with Vertical(classes="log-section"):
            yield ExecutionLog(id="execution-log")

    def show_waypoint(
        self,
        waypoint: Waypoint | None,
        project: "Project | None" = None,
        active_waypoint_id: str | None = None,
        cost: float | None = None,
    ) -> None:
        """Display waypoint details.

        Args:
            waypoint: The waypoint to display
            project: The project (for loading completed criteria from log)
            active_waypoint_id: ID of the currently executing waypoint
            cost: Optional cost in USD for this waypoint
        """
        self._waypoint = waypoint
        self._waypoint_cost = cost

        title = self.query_one("#wp-title", Static)
        objective = self.query_one("#wp-objective", Static)
        status = self.query_one("#wp-status", Static)

        # Criteria list might not exist yet if called before compose
        try:
            criteria_list = self.query_one("#criteria-list", AcceptanceCriteriaList)
        except Exception:
            criteria_list = None

        if waypoint:
            title.update(f"{waypoint.id}: {waypoint.title}")
            obj_text = waypoint.objective
            if len(obj_text) > 100:
                obj_text = obj_text[:97] + "..."
            objective.update(obj_text)

            # Format status with colored icon
            icon = get_status_markup(waypoint.status)
            label = get_status_label(waypoint.status)
            status.update(Text.from_markup(f"{icon} {label}"))

            # Load completed criteria from execution log for completed waypoints
            completed: set[int] | None = None
            if project and waypoint.status.value == "complete":
                from waypoints.fly.execution_log import ExecutionLogReader

                completed = ExecutionLogReader.get_completed_criteria(
                    project, waypoint.id
                )

            # Update acceptance criteria
            if criteria_list:
                criteria_list.set_criteria(waypoint.acceptance_criteria, completed)

            # Update output based on whether this is the active waypoint
            self._update_output_for_waypoint(waypoint, active_waypoint_id)
        else:
            title.update("Select a waypoint")
            objective.update("")
            status.update("–")
            if criteria_list:
                criteria_list.set_criteria([])
            self.clear_iteration()
            self.execution_log.clear_log()
            self._showing_output_for = None
            self._is_live_output = False

    def _update_output_for_waypoint(
        self, waypoint: Waypoint, active_waypoint_id: str | None
    ) -> None:
        """Update the output panel based on waypoint status."""
        log = self.execution_log

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
                log.write_log(f"Completed: {completed}")
            log.write_log("(No execution log found)")
        elif waypoint.status == WaypointStatus.FAILED:
            log.log_error("Last execution failed")
            log.write_log("Press 'r' to retry")
            log.write_log("(No execution log found)")
        elif waypoint.status == WaypointStatus.IN_PROGRESS:
            # In progress but not active (stale from previous session)
            log.write_log("Execution was in progress...")
            log.write_log("(Session may have been interrupted)")
        else:  # PENDING
            log.write_log("Waiting to execute")
            if waypoint.dependencies:
                deps = ", ".join(waypoint.dependencies)
                log.write_log(f"Dependencies: {deps}")

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

            log = self.execution_log

            # Log the three main sections
            self._log_execution_header(log, exec_log)
            max_iteration = self._log_execution_entries(log, exec_log)
            self._update_iteration_stats(exec_log, max_iteration)
            self._log_historical_verification(waypoint, log)

            return True

        except Exception as e:
            logger.warning(
                "Failed to load execution history for %s: %s", waypoint.id, e
            )
            return False

    def _log_execution_header(
        self, log: "ExecutionLog", exec_log: "ExecLogType"
    ) -> None:
        """Log execution summary header."""
        log.log_heading(f"Execution Log · {exec_log.execution_id[:8]}")
        started = exec_log.started_at.strftime("%Y-%m-%d %H:%M")
        log.write_log(f"Started: {started}")

        if exec_log.completed_at:
            completed = exec_log.completed_at.strftime("%Y-%m-%d %H:%M")
            duration = (exec_log.completed_at - exec_log.started_at).seconds
            log.write_log(f"Completed: {completed} ({duration}s)")

        if exec_log.result:
            if exec_log.result == "success":
                log.log_success(f"Result: {exec_log.result}")
            else:
                log.log_error(f"Result: {exec_log.result}")

        if exec_log.total_cost_usd > 0:
            log.write_log(f"Cost: ${exec_log.total_cost_usd:.4f}")

        log.write_log("")  # Blank line

    def _log_execution_entries(
        self, log: "ExecutionLog", exec_log: "ExecLogType"
    ) -> int:
        """Log all execution entries and return max iteration count."""
        max_iteration = 0

        for entry in exec_log.entries:
            entry_type = entry.entry_type

            if entry_type == "iteration_start":
                max_iteration = max(max_iteration, entry.iteration)
                log.log_heading(f"Iteration {entry.iteration}")

            elif entry_type == "output":
                if entry.content:
                    content = entry.content
                    if len(content) > 24000:
                        content = content[:24000] + "\n... (truncated)"
                    log.write_log(content)

            elif entry_type == "error":
                log.log_error(entry.content)

            elif entry_type == "iteration_end":
                cost = entry.metadata.get("cost_usd")
                if cost:
                    log.write_log(f"(Iteration cost: ${cost:.4f})")

            elif entry_type == "tool_call":
                self._log_tool_call_entry(log, entry)

            elif entry_type == "intervention_needed":
                int_type = entry.metadata.get("intervention_type", "unknown")
                reason = entry.metadata.get("reason", "")
                log.write_log(
                    f"[yellow]⚠ Intervention needed ({int_type}): {reason}[/]"
                )

            elif entry_type == "intervention_resolved":
                action = entry.metadata.get("action", "unknown")
                log.write_log(f"[green]✓ Intervention resolved: {action}[/]")

            elif entry_type == "completion_detected":
                log.write_log("[green]✓ Completion marker detected[/]")

            elif entry_type == "receipt_validated":
                valid = entry.metadata.get("valid", False)
                if valid:
                    log.write_log("[green]✓ Receipt validated[/]")
                else:
                    msg = entry.metadata.get("message", "")
                    log.write_log(f"[yellow]⚠ Receipt invalid: {msg}[/]")

            elif entry_type == "git_commit":
                success = entry.metadata.get("success", False)
                if success:
                    hash_ = entry.metadata.get("commit_hash", "")
                    log.write_log(f"[green]✓ Git commit: {hash_}[/]")
                else:
                    msg = entry.metadata.get("message", "")
                    log.write_log(f"[red]✗ Git commit failed: {msg}[/]")

            elif entry_type == "pause":
                log.write_log("[yellow]⏸ Execution paused[/]")

            elif entry_type == "security_violation":
                details = entry.metadata.get("details", "")
                log.write_log(f"[red]⚠ Security violation: {details}[/]")

        return max_iteration

    def _log_tool_call_entry(self, log: "ExecutionLog", entry: Any) -> None:
        """Log a tool call entry with clickable file paths for file operations."""
        tool_name = entry.metadata.get("tool_name", "unknown")
        tool_input = entry.metadata.get("tool_input", {})

        # Show clickable paths for file operations
        if tool_name in ("Edit", "Write", "Read") and isinstance(tool_input, dict):
            file_path = tool_input.get("file_path")
            if file_path:
                icon = {"Edit": "✎", "Write": "✚", "Read": "📖"}.get(tool_name, "•")
                style = "dim" if tool_name == "Read" else "cyan"
                escaped_path = file_path.replace("'", "\\'")
                markup = (
                    f"  [{style}]{icon}[/] "
                    f"[@click=screen.preview_file('{escaped_path}')]"
                    f"[{style} underline]{file_path}[/][/]"
                )
                log.write(markup)
                return

        # Fallback for other tools
        log.write_log(f"[dim]→ {tool_name}[/]")

    def _update_iteration_stats(
        self, exec_log: "ExecLogType", max_iteration: int
    ) -> None:
        """Update iteration label with execution statistics."""
        from waypoints.tui.utils import format_duration

        if max_iteration <= 0:
            return

        s = "s" if max_iteration > 1 else ""
        parts = [f"{max_iteration} iteration{s}"]

        # Add duration
        if exec_log.completed_at and exec_log.started_at:
            duration = int((exec_log.completed_at - exec_log.started_at).total_seconds())
            parts.append(format_duration(duration))

        # Add cost from metrics collector (or fall back to execution log)
        cost = self._waypoint_cost or exec_log.total_cost_usd
        if cost and cost > 0:
            parts.append(f"${cost:.2f}")

        # Add start time
        started = exec_log.started_at.strftime("%b %d, %H:%M")
        parts.append(f"started {started}")

        self.query_one("#iteration-label", Static).update(" · ".join(parts))

    def _log_historical_verification(
        self, waypoint: Waypoint, log: "ExecutionLog"
    ) -> None:
        """Log verification summary for historical waypoints.

        Similar to _log_verification_summary but uses persisted data
        instead of live tracking.
        """
        log.log_heading("Verification Summary")

        # Get completed criteria from execution log
        completed_criteria = ExecutionLogReader.get_completed_criteria(
            self._project, waypoint.id
        )
        total_criteria = len(waypoint.acceptance_criteria)
        completed_count = len(completed_criteria)

        if total_criteria > 0:
            for i, criterion in enumerate(waypoint.acceptance_criteria):
                if i in completed_criteria:
                    log.write_log(f"[green]✓[/] {criterion}")
                else:
                    log.write_log(f"[yellow]?[/] {criterion} [dim](not marked)[/]")

            if completed_count == total_criteria:
                log.write_log(f"\n[green]All {total_criteria} criteria verified[/]")
            else:
                log.write_log(
                    f"\n[yellow]{completed_count}/{total_criteria} criteria marked[/]"
                )

        # Check receipt status
        validator = ReceiptValidator()
        receipt_path = validator.find_latest_receipt(self._project, waypoint.id)

        if receipt_path:
            result = validator.validate(receipt_path)
            if result.valid:
                log.write_log("[green]✓ Receipt validated[/]")
            else:
                log.write_log(f"[yellow]⚠ Receipt: {result.message}[/]")
                if result.receipt:
                    for item in result.receipt.failed_items():
                        log.write_log(f"  [red]✗[/] {item.item}: {item.evidence}")
        else:
            log.write_log("[yellow]⚠ No receipt found[/]")

    def _show_epic_details(self, waypoint: Waypoint) -> None:
        """Display details for an epic (multi-hop waypoint with children).

        Shows the epic's children and their status instead of execution output.

        Args:
            waypoint: The epic waypoint to display
        """
        log = self.execution_log
        children = self._flight_plan.get_children(waypoint.id)

        log.log_heading("Multi-hop Waypoint")
        log.write_log(f"This waypoint contains {len(children)} child tasks.")
        log.write_log("")

        # Calculate progress
        complete = sum(1 for c in children if c.status == WaypointStatus.COMPLETE)
        failed = sum(1 for c in children if c.status == WaypointStatus.FAILED)
        in_progress = sum(1 for c in children if c.status == WaypointStatus.IN_PROGRESS)

        # Progress summary
        if complete == len(children):
            log.log_success(f"Progress: {complete}/{len(children)} complete")
        elif failed > 0:
            log.write_log(
                f"Progress: {complete}/{len(children)} complete, {failed} failed"
            )
        elif in_progress > 0:
            log.write_log(
                f"Progress: {complete}/{len(children)} complete, 1 in progress"
            )
        else:
            log.write_log(f"Progress: {complete}/{len(children)} complete")

        log.write_log("")
        log.write_log("Children:")

        # Show each child
        for child in children:
            icon = get_status_icon(child.status)
            style = get_status_color(child.status)
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

    def update_criteria(self, completed: set[int]) -> None:
        """Update which acceptance criteria are marked complete."""
        criteria_list = self.query_one("#criteria-list", AcceptanceCriteriaList)
        criteria_list.update_completed(completed)

    @property
    def execution_log(self) -> ExecutionLog:
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

    WaypointListPanel .git-status {
        color: $text-muted;
        margin-top: 0;
    }

    WaypointListPanel .project-metrics {
        color: $text-muted;
        margin-top: 0;
    }

    WaypointListPanel .panel-footer {
        dock: bottom;
        height: auto;
    }

    WaypointListPanel .action-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    WaypointListPanel .legend {
        height: auto;
        padding: 1;
        border-top: solid $surface-lighten-1;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
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
            yield Static("", classes="git-status", id="git-status")
            yield Static("", classes="project-metrics", id="project-metrics")
        yield FlightPlanTree(id="waypoint-tree")
        with Vertical(classes="panel-footer"):
            yield Static("", classes="action-hint", id="action-hint")
            yield Static("◉ Done  ◎ Active  ✗ Failed  ○ Pending", classes="legend")

    def update_action_hint(self, message: str) -> None:
        """Update the action hint text."""
        self.query_one("#action-hint", Static).update(message)

    def update_git_status(self, message: str) -> None:
        """Update the git status indicator."""
        self.query_one("#git-status", Static).update(message)

    def update_project_metrics(self, cost: float, time_seconds: int) -> None:
        """Update the project metrics display (cost and time).

        Args:
            cost: Total cost in USD.
            time_seconds: Total execution time in seconds.
        """
        parts = []
        if cost > 0:
            parts.append(f"${cost:.2f}")
        if time_seconds > 0:
            mins, secs = divmod(time_seconds, 60)
            if mins >= 60:
                hours, mins = divmod(mins, 60)
                parts.append(f"{hours}h {mins}m")
            elif mins > 0:
                parts.append(f"{mins}m {secs}s")
            else:
                parts.append(f"{secs}s")

        display = " · ".join(parts) if parts else ""
        self.query_one("#project-metrics", Static).update(display)

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


class FlyScreen(Screen[None]):
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
        coordinator: JourneyCoordinator | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project = project
        self.flight_plan = flight_plan
        self.spec = spec

        # Use provided coordinator or create one
        self.coordinator = coordinator or JourneyCoordinator(
            project=project,
            flight_plan=flight_plan,
        )

        self._executor: WaypointExecutor | None = None
        self._current_intervention: Intervention | None = None
        self._additional_iterations: int = 0
        # Timer tracking
        self._execution_start: datetime | None = None
        self._elapsed_before_pause: float = 0.0
        self._ticker_timer: Timer | None = None
        # Track live criteria completion for cross-check with receipt
        self._live_criteria_completed: set[int] = set()

    @property
    def waypoints_app(self) -> "WaypointsApp":
        """Get the app as WaypointsApp for type checking."""
        return cast("WaypointsApp", self.app)

    @property
    def current_waypoint(self) -> Waypoint | None:
        """Get the currently selected waypoint (delegated to coordinator)."""
        return self.coordinator.current_waypoint

    @current_waypoint.setter
    def current_waypoint(self, waypoint: Waypoint | None) -> None:
        """Set the currently selected waypoint (delegated to coordinator)."""
        self.coordinator.current_waypoint = waypoint

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        yield ResizableSplit(
            left=WaypointListPanel(id="waypoint-list"),
            right=WaypointDetailPanel(
                project=self.project,
                flight_plan=self.flight_plan,
                id="waypoint-detail",
            ),
            left_pct=33,
            classes="main-container",
        )
        yield Static(
            "Press Space to start execution", classes="status-bar", id="status-bar"
        )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the screen."""
        self.app.sub_title = f"{self.project.name} · Fly"

        # Set up metrics collection for this project
        self.waypoints_app.set_project_for_metrics(self.project)

        # Clean up stale IN_PROGRESS from previous sessions (via coordinator)
        self.coordinator.reset_stale_in_progress()

        # Update waypoint list with cost data
        self._refresh_waypoint_list()

        # Select resumable waypoint (failed/in-progress) or first pending
        self._select_next_waypoint(include_in_progress=True)

        # Update status bar with initial state (watcher doesn't fire on mount)
        self._update_status_bar(self.execution_state)

        wp_count = len(self.flight_plan.waypoints)
        logger.info("FlyScreen mounted with %d waypoints", wp_count)

        # Start git status polling
        self._update_git_status()
        self._git_status_timer = self.set_interval(10.0, self._update_git_status)

        # Update project metrics (cost and time)
        self._update_project_metrics()

    def _update_git_status(self) -> None:
        """Update git status indicator in the left panel."""
        status = get_git_status_summary(self.project.get_path())
        list_panel = self.query_one(WaypointListPanel)
        list_panel.update_git_status(status)

    def _calculate_total_execution_time(self) -> int:
        """Calculate total execution time across all waypoints in seconds."""
        total_seconds = 0
        log_files = ExecutionLogReader.list_logs(self.project)
        for log_path in log_files:
            try:
                log = ExecutionLogReader.load(log_path)
                if log.completed_at and log.started_at:
                    total_seconds += int(
                        (log.completed_at - log.started_at).total_seconds()
                    )
            except Exception:
                continue
        return total_seconds

    def _update_project_metrics(self) -> None:
        """Update project-wide cost and time metrics in the left panel."""
        cost = 0.0
        if self.waypoints_app.metrics_collector:
            cost = self.waypoints_app.metrics_collector.total_cost
        time_seconds = self._calculate_total_execution_time()
        list_panel = self.query_one(WaypointListPanel)
        list_panel.update_project_metrics(cost, time_seconds)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[Waypoint]) -> None:
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
            cost = self._get_waypoint_cost(waypoint.id)
            detail_panel.show_waypoint(
                waypoint, project=self.project, active_waypoint_id=active_id, cost=cost
            )

    def _get_waypoint_cost(self, waypoint_id: str) -> float | None:
        """Get the cost for a waypoint from the metrics collector.

        Args:
            waypoint_id: The waypoint ID to get cost for

        Returns:
            Cost in USD, or None if not available
        """
        if self.waypoints_app.metrics_collector:
            cost_by_waypoint = self.waypoints_app.metrics_collector.cost_by_waypoint()
            return cost_by_waypoint.get(waypoint_id)
        return None

    def _get_completion_status(self) -> tuple[bool, int, int, int]:
        """Analyze waypoint completion status.

        Returns:
            Tuple of (all_complete, pending_count, failed_count, blocked_count)
        """
        status = self.coordinator.get_completion_status()
        # Include in_progress in pending count for legacy compatibility
        pending = status.pending + status.in_progress
        all_complete = status.all_complete
        return (all_complete, pending, status.failed, status.blocked)

    def _select_next_waypoint(self, include_in_progress: bool = False) -> None:
        """Find and select the next waypoint to execute.

        Delegates selection logic to the coordinator, then updates UI.

        Args:
            include_in_progress: If True, also consider IN_PROGRESS and FAILED
                                waypoints (for resume after pause/failure)
        """
        logger.debug(
            "=== Selection round (include_in_progress=%s) ===", include_in_progress
        )

        # Delegate selection to coordinator
        wp = self.coordinator.select_next_waypoint(include_failed=include_in_progress)

        if wp:
            # Waypoint selected - update UI
            logger.info("SELECTED %s via coordinator", wp.id)
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            cost = self._get_waypoint_cost(wp.id)
            detail_panel.show_waypoint(
                wp, project=self.project, active_waypoint_id=None, cost=cost
            )
            return

        # No eligible waypoints found - check why
        all_complete, pending, failed, blocked = self._get_completion_status()

        if all_complete:
            logger.info("All waypoints complete - DONE")
            self.execution_state = ExecutionState.DONE
        elif blocked > 0:
            logger.info("Waypoints blocked by %d failed waypoint(s)", failed)
            self.execution_state = ExecutionState.PAUSED
        elif pending > 0:
            logger.info("%d waypoints pending with unmet dependencies", pending)
            self.execution_state = ExecutionState.PAUSED
        else:
            # Only failed waypoints remain
            logger.info("Only failed waypoints remain (%d)", failed)
            self.execution_state = ExecutionState.PAUSED

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
                # If current waypoint failed, 'r' will skip to next
                if self.current_waypoint.status == WaypointStatus.FAILED:
                    return f"{self.current_waypoint.id} failed. Press 'r' to continue"
                return f"Paused. Press 'r' to run {self.current_waypoint.id}"
            # No current waypoint - show why we're paused
            all_complete, pending, failed, blocked = self._get_completion_status()
            if blocked > 0:
                return f"Blocked · {blocked} waypoint(s) need failed deps fixed"
            elif pending > 0:
                return f"Paused · {pending} waypoint(s) waiting"
            return "Paused. Press 'r' to continue"
        elif state == ExecutionState.DONE:
            # Verify all waypoints are truly complete
            all_complete, pending, failed, blocked = self._get_completion_status()
            if all_complete:
                return "All waypoints complete!"
            elif blocked > 0:
                return f"{blocked} waypoint(s) blocked by failures"
            elif failed > 0:
                return f"{failed} waypoint(s) failed"
            return f"{pending} waypoint(s) waiting"
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
            self.waypoints_app.metrics_collector.total_cost
            if self.waypoints_app.metrics_collector
            else 0.0
        )

        status_bar = self.query_one("#status-bar", Static)
        message = self._get_state_message(self.execution_state)
        status_bar.update(f"⏱ {minutes}:{seconds:02d} | ${cost:.2f}    {message}")

    def _update_status_bar(self, state: ExecutionState) -> None:
        """Update the status bar with state message and optional cost."""
        status_bar = self.query_one("#status-bar", Static)
        message = self._get_state_message(state)

        # Update action hint in left panel
        list_panel = self.query_one(WaypointListPanel)
        list_panel.update_action_hint(message)

        if state == ExecutionState.RUNNING and self._execution_start:
            # Timer callback will handle updates
            return

        # Show cost even when not running (if there's any)
        cost = (
            self.waypoints_app.metrics_collector.total_cost
            if self.waypoints_app.metrics_collector
            else 0.0
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
        # Check if user has selected a specific failed waypoint to retry
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        selected = list_panel.selected_waypoint

        if selected and selected.status == WaypointStatus.FAILED:
            # User wants to retry this specific failed waypoint
            selected.status = WaypointStatus.PENDING
            self._save_flight_plan()
            self._refresh_waypoint_list()
            self.current_waypoint = selected
            self.notify(f"Retrying {selected.id}")

            # Update detail panel to show this waypoint
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            cost = self._get_waypoint_cost(selected.id)
            detail_panel.show_waypoint(
                selected, project=self.project, active_waypoint_id=None, cost=cost
            )

            # Transition journey state and execute
            journey = self.project.journey
            if journey and journey.state in (
                JourneyState.FLY_PAUSED,
                JourneyState.FLY_INTERVENTION,
            ):
                self.project.transition_journey(JourneyState.FLY_EXECUTING)
            elif journey and journey.state == JourneyState.CHART_REVIEW:
                self.project.transition_journey(JourneyState.FLY_READY)
                self.project.transition_journey(JourneyState.FLY_EXECUTING)
            else:
                self.project.transition_journey(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            self._execute_current_waypoint()
            return

        if self.execution_state == ExecutionState.DONE:
            # Check if there are actually failed waypoints to retry
            _, _, failed, blocked = self._get_completion_status()
            if failed > 0 or blocked > 0:
                self.notify("Select a failed waypoint and press 'r' to retry")
            else:
                self.notify("All waypoints complete!")
            return

        # Handle resume from paused state
        if self.execution_state == ExecutionState.PAUSED:
            # Find waypoint to resume (in_progress first, then pending)
            self._select_next_waypoint(include_in_progress=True)
            if not self.current_waypoint:
                # Check if there are failed waypoints user could retry
                _, _, failed, blocked = self._get_completion_status()
                if failed > 0:
                    self.notify("Select a failed waypoint and press 'r' to retry")
                else:
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
                # Log pause request
                if self._executor._log_writer:
                    self._executor._log_writer.log_pause()
            self.notify("Will pause after current waypoint")

    def action_skip(self) -> None:
        """Skip the current waypoint."""
        if self.current_waypoint:
            wp_id = self.current_waypoint.id
            self.notify(f"Skipped {wp_id}")
            self._select_next_waypoint()

    def action_preview_file(self, path: str) -> None:
        """Show file preview modal for a file path.

        Args:
            path: File path to preview (relative to project or absolute)
        """
        # Resolve relative paths against project root
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = self.project.get_path() / path

        # Push the preview modal
        self.app.push_screen(FilePreviewModal(file_path))

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
        self.waypoints_app.switch_phase(
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
        log = detail_panel.execution_log

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
            metrics_collector=self.waypoints_app.metrics_collector,
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
        """Handle progress updates from the executor.

        Uses call_later to safely schedule UI updates from any thread context.
        """
        self.app.call_later(self._update_progress_ui, ctx)

    def _update_progress_ui(self, ctx: ExecutionContext) -> None:
        """Update UI with progress (called on main thread)."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)

        # Guard: Only update if this waypoint's output is currently displayed
        if detail_panel._showing_output_for != ctx.waypoint.id:
            return

        log = detail_panel.execution_log

        # Update iteration display
        detail_panel.update_iteration(ctx.iteration, ctx.total_iterations)

        # Update acceptance criteria checkboxes
        if ctx.criteria_completed:
            self._live_criteria_completed = ctx.criteria_completed
            detail_panel.update_criteria(ctx.criteria_completed)

        # Log based on step type
        if ctx.step == "executing":
            log.log_heading(f"Iteration {ctx.iteration}/{ctx.total_iterations}")
        elif ctx.step == "tool_use":
            # Display file operation with icon (clickable for file operations)
            if ctx.file_operations:
                op: FileOperation = ctx.file_operations[-1]  # Get the latest op
                icon = {
                    "Edit": "✎",
                    "Write": "✚",
                    "Read": "📖",
                    "Bash": "$",
                    "Glob": "🔍",
                    "Grep": "🔍",
                }.get(op.tool_name, "•")
                style = "dim" if op.tool_name == "Read" else "cyan"
                # Format the file operation line - make file paths clickable
                if op.file_path:
                    # Escape quotes in path for action parameter
                    escaped_path = op.file_path.replace("'", "\\'")
                    if op.tool_name in ("Edit", "Write", "Read"):
                        # File operations are clickable - use string markup for @click
                        markup = (
                            f"  [{style}]{icon}[/] "
                            f"[@click=screen.preview_file('{escaped_path}')]"
                            f"[{style} underline]{op.file_path}[/][/]"
                        )
                        # Write string directly so Textual parses @click
                        log.write(markup)
                    else:
                        # Bash/Glob/Grep just show the command/pattern
                        text = f"  [{style}]{icon}[/] {op.file_path}"
                        log.write(Text.from_markup(text))
        elif ctx.step == "streaming":
            # Show streaming output (code blocks will be syntax-highlighted)
            output = ctx.output.strip()
            if output:
                log.write_log(output)
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
        log = detail_panel.execution_log

        # Update header cost display after execution
        self.waypoints_app.update_header_cost()

        # Update project metrics (cost and time) after execution
        self._update_project_metrics()

        if result == ExecutionResult.SUCCESS:
            # Mark complete
            if self.current_waypoint:
                self.current_waypoint.status = WaypointStatus.COMPLETE
                self.current_waypoint.completed_at = datetime.now()
                log.log_success(f"Waypoint {self.current_waypoint.id} complete!")

                # Show verification summary
                self._log_verification_summary(self.current_waypoint, log)

                # Check if parent epic should be auto-completed
                self._check_parent_completion(self.current_waypoint)

                self._save_flight_plan()

                # Commit waypoint completion (validates receipt first)
                self._commit_waypoint(self.current_waypoint)

                # Reset live criteria tracking for next waypoint
                self._live_criteria_completed = set()

            detail_panel.clear_iteration()
            self._refresh_waypoint_list()

            # Move to next waypoint if not paused/pausing
            if self.execution_state == ExecutionState.RUNNING:
                self._select_next_waypoint()
                if self.current_waypoint:
                    self._execute_current_waypoint()
                else:
                    # _select_next_waypoint sets execution_state appropriately
                    # Only transition to LANDED if truly all complete (state is DONE)
                    # Note: mypy doesn't track that _select_next_waypoint modifies state
                    if self.execution_state == ExecutionState.DONE:  # type: ignore[comparison-overlap]
                        self.project.transition_journey(JourneyState.LANDED)
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
            log.write_log("Execution cancelled")
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
        log = detail_panel.execution_log

        # Log the intervention
        type_label = intervention.type.value.replace("_", " ").title()
        log.log_error(f"Intervention needed: {type_label}")
        log.write_log(intervention.error_summary[:500])

        # Store the intervention for retry handling
        self._current_intervention = intervention

        # Mark waypoint as failed (can be retried)
        self._mark_waypoint_failed()

        # Transition journey state: FLY_EXECUTING -> FLY_INTERVENTION
        self.project.transition_journey(JourneyState.FLY_INTERVENTION)
        self.execution_state = ExecutionState.INTERVENTION
        self.query_one(StatusHeader).set_error()

        # Show the intervention modal
        self.app.push_screen(
            InterventionModal(intervention),
            callback=self._on_intervention_result,
        )

    def _on_intervention_result(self, result: InterventionResult | None) -> None:
        """Handle the result of the intervention modal."""
        if result is None:
            # User cancelled - treat as abort
            self.notify("Intervention cancelled")
            # Log cancellation
            if self._executor and self._executor._log_writer:
                self._executor._log_writer.log_intervention_resolved("cancelled")
            return

        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        # Log the intervention resolution to execution log
        if self._executor and self._executor._log_writer:
            params: dict[str, Any] = {}
            if result.action == InterventionAction.RETRY:
                params["additional_iterations"] = result.additional_iterations
            elif result.action == InterventionAction.ROLLBACK:
                params["rollback_tag"] = result.rollback_tag
            self._executor._log_writer.log_intervention_resolved(
                result.action.value, **params
            )

        if result.action == InterventionAction.RETRY:
            # Retry with additional iterations
            log.write_log(
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
            log.write_log("Skipping waypoint")
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
                # _select_next_waypoint sets execution_state appropriately
                # Only transition to LANDED and notify if truly all complete
                if self.execution_state == ExecutionState.DONE:
                    self.project.transition_journey(JourneyState.LANDED)
                    self.notify("All waypoints complete!")

        elif result.action == InterventionAction.EDIT:
            # Open waypoint editor - for now, just notify
            log.write_log("Edit waypoint requested")
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
            log.write_log("Rolling back to last safe tag")
            self._rollback_to_safe_tag(result.rollback_tag)
            # Transition: FLY_INTERVENTION -> FLY_READY
            self.project.transition_journey(JourneyState.FLY_PAUSED)
            self.project.transition_journey(JourneyState.FLY_READY)
            self.execution_state = ExecutionState.IDLE
            self.query_one(StatusHeader).set_normal()

        elif result.action == InterventionAction.ABORT:
            # Abort execution
            log.write_log("Execution aborted")
            # Transition: FLY_INTERVENTION -> FLY_PAUSED
            self.project.transition_journey(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self.query_one(StatusHeader).set_normal()
            self.notify("Execution aborted")

        # Clear the current intervention
        self._current_intervention = None

    def _rollback_to_safe_tag(self, tag: str | None) -> None:
        """Rollback git to the specified tag or find the last safe one."""
        git = GitService(self.project.get_path())

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
        result = git.reset_hard(target_tag)
        if result.success:
            self.notify(f"Rolled back to {target_tag}")
            # Reload flight plan from disk after reset
            flight_plan_path = self.project.get_path() / "flight-plan.jsonl"
            if flight_plan_path.exists():
                from waypoints.models.flight_plan import FlightPlanReader

                loaded = FlightPlanReader.load(self.project)
                if loaded:
                    self.flight_plan = loaded
                    self._refresh_waypoint_list()
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
        if self.waypoints_app.metrics_collector:
            cost_by_waypoint = self.waypoints_app.metrics_collector.cost_by_waypoint()
        list_panel.update_flight_plan(
            self.flight_plan, execution_state, cost_by_waypoint
        )

    def _save_flight_plan(self) -> None:
        """Save the flight plan to disk."""
        writer = FlightPlanWriter(self.project)
        writer.save(self.flight_plan)

    def _log_verification_summary(self, waypoint: Waypoint, log: ExecutionLog) -> None:
        """Log verification summary comparing live criteria with receipt."""
        log.log_heading("Verification Summary")

        # Report live acceptance criteria status
        total_criteria = len(waypoint.acceptance_criteria)
        live_completed = len(self._live_criteria_completed)

        if total_criteria > 0:
            for i, criterion in enumerate(waypoint.acceptance_criteria):
                if i in self._live_criteria_completed:
                    log.write_log(f"[green]✓[/] {criterion}")
                else:
                    log.write_log(f"[yellow]?[/] {criterion} [dim](not marked)[/]")

            if live_completed == total_criteria:
                log.write_log(f"\n[green]All {total_criteria} criteria verified[/]")
            else:
                log.write_log(
                    f"\n[yellow]{live_completed}/{total_criteria} criteria marked[/]"
                )

        # Check receipt status
        validator = ReceiptValidator()
        receipt_path = validator.find_latest_receipt(self.project, waypoint.id)

        if receipt_path:
            result = validator.validate(receipt_path)
            if result.valid:
                log.write_log("[green]✓ Receipt validated[/]")
            else:
                log.write_log(f"[yellow]⚠ Receipt: {result.message}[/]")
                if result.receipt:
                    for item in result.receipt.failed_items():
                        log.write_log(f"  [red]✗[/] {item.item}: {item.evidence}")
        else:
            log.write_log("[yellow]⚠ No receipt found[/]")

    def _commit_waypoint(self, waypoint: Waypoint) -> None:
        """Commit waypoint completion if receipt is valid.

        Implements the "trust but verify" pattern:
        - Model already produced receipt during execution
        - We validate receipt exists and is well-formed
        - If valid, commit the changes
        - If invalid, skip commit but don't block
        """
        project_path = self.project.get_path()
        config = GitConfig.load(self.project.slug)

        if not config.auto_commit:
            logger.debug("Auto-commit disabled, skipping")
            return

        git = GitService(project_path)

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
            receipt_path = validator.find_latest_receipt(self.project, waypoint.id)

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
                # Log successful git commit
                if self._executor and self._executor._log_writer:
                    commit_hash = git.get_head_commit() or ""
                    self._executor._log_writer.log_git_commit(
                        True, commit_hash, commit_msg
                    )

                # Create tag for waypoint if configured
                if config.create_waypoint_tags:
                    tag_name = f"{self.project.slug}/{waypoint.id}"
                    git.tag(tag_name, f"Completed waypoint: {waypoint.title}")
        else:
            logger.error("Commit failed: %s", result.message)
            self.notify(f"Commit failed: {result.message}", severity="error")
            # Log failed git commit
            if self._executor and self._executor._log_writer:
                self._executor._log_writer.log_git_commit(False, "", result.message)

    def _check_parent_completion(self, completed_waypoint: Waypoint) -> None:
        """Check if parent epic is ready for execution.

        Delegates to coordinator. Note: parents are no longer auto-completed;
        they will be selected and executed to verify their acceptance criteria.

        Args:
            completed_waypoint: The waypoint that just completed
        """
        # Delegate to coordinator - it logs readiness but doesn't auto-complete
        self.coordinator._check_parent_completion(completed_waypoint)
