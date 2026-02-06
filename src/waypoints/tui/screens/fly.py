"""FLY screen for waypoint implementation."""

import logging
import re
import subprocess
from datetime import UTC, datetime
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
from textual.worker import Worker, WorkerFailed

if TYPE_CHECKING:
    from waypoints.git.receipt import ChecklistReceipt
    from waypoints.tui.app import WaypointsApp

from waypoints.fly.execution_log import ExecutionLog as ExecLogType
from waypoints.fly.execution_log import (
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
from waypoints.fly.state import ExecutionState
from waypoints.git import GitService, ReceiptValidator
from waypoints.models import JourneyState, Project
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import ExecutionController, JourneyCoordinator
from waypoints.tui.screens.intervention import InterventionModal
from waypoints.tui.utils import (
    format_token_count,
    get_status_color,
    get_status_icon,
    get_status_label,
    get_status_markup,
)
from waypoints.tui.widgets.file_preview import FilePreviewModal
from waypoints.tui.widgets.flight_plan import DebugWaypointModal, FlightPlanTree
from waypoints.tui.widgets.header import StatusHeader
from waypoints.tui.widgets.resizable_split import (
    ResizableSplit,
    ResizableSplitVertical,
)

logger = logging.getLogger(__name__)


def _format_project_metrics(
    cost: float,
    time_seconds: int,
    tokens_in: int | None,
    tokens_out: int | None,
    tokens_known: bool,
) -> str:
    parts: list[str] = []
    if cost > 0:
        parts.append(f"${cost:.2f}")
    if tokens_known or tokens_in or tokens_out:
        parts.append(
            "Tokens: "
            f"{format_token_count(tokens_in or 0)} in / "
            f"{format_token_count(tokens_out or 0)} out"
        )
    elif cost > 0 or time_seconds > 0:
        parts.append("Tokens: n/a")
    if time_seconds > 0:
        mins, secs = divmod(time_seconds, 60)
        if mins >= 60:
            hours, mins = divmod(mins, 60)
            parts.append(f"{hours}h {mins}m")
        elif mins > 0:
            parts.append(f"{mins}m {secs}s")
        else:
            parts.append(f"{secs}s")
    return " · ".join(parts) if parts else ""


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

    WaypointDetailPanel .wp-notes {
        color: $text-muted;
    }

    WaypointDetailPanel .wp-status {
        color: $text-muted;
    }

    WaypointDetailPanel .wp-metrics {
        color: $text-muted;
    }

    WaypointDetailPanel .criteria-section {
        padding: 1 1 1 0;
        border-bottom: none;
    }

    WaypointDetailPanel .section-label {
        color: $text-muted;
    }

    WaypointDetailPanel .log-section {
        height: 100%;
    }

    WaypointDetailPanel .detail-split {
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
        self._waypoint_tokens: tuple[int, int] | None = None
        self._showing_output_for: str | None = None  # Track which waypoint's output
        self._is_live_output: bool = False  # True if showing live streaming output

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("Select a waypoint", classes="wp-title", id="wp-title")
            yield Static("", classes="wp-objective", id="wp-objective")
            yield Static("", classes="wp-notes", id="wp-notes")
            yield Static("Status: Pending", classes="wp-status", id="wp-status")
            yield Static("", classes="wp-metrics", id="wp-metrics")
            yield Static("", classes="wp-status", id="iteration-label")
            yield ResizableSplitVertical(
                top=Vertical(
                    Static("Acceptance Criteria", classes="section-label"),
                    AcceptanceCriteriaList(id="criteria-list"),
                    classes="criteria-section",
                    id="criteria-section",
                ),
                bottom=Vertical(
                    ExecutionLog(id="execution-log"),
                    classes="log-section",
                    id="log-section",
                ),
                top_pct=45,
                classes="detail-split",
            )

    def show_waypoint(
        self,
        waypoint: Waypoint | None,
        project: "Project | None" = None,
        active_waypoint_id: str | None = None,
        cost: float | None = None,
        tokens: tuple[int, int] | None = None,
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
        self._waypoint_tokens = tokens

        title = self.query_one("#wp-title", Static)
        objective = self.query_one("#wp-objective", Static)
        notes = self.query_one("#wp-notes", Static)
        status = self.query_one("#wp-status", Static)
        metrics = self.query_one("#wp-metrics", Static)

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
            if waypoint.resolution_notes:
                notes_text = "; ".join(waypoint.resolution_notes)
                if len(notes_text) > 120:
                    notes_text = notes_text[:117] + "..."
                notes.update(f"Notes: {notes_text}")
            else:
                notes.update("")

            # Format status with colored icon
            icon = get_status_markup(waypoint.status)
            label = get_status_label(waypoint.status)
            status.update(Text.from_markup(f"{icon} {label}"))
            metrics.update(self._format_metrics_line(cost, tokens))

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
            notes.update("")
            status.update("–")
            metrics.update("")
            if criteria_list:
                criteria_list.set_criteria([])
            self.clear_iteration()
            self.execution_log.clear_log()
            self._showing_output_for = None
            self._is_live_output = False

    def update_metrics(
        self, cost: float | None, tokens: tuple[int, int] | None
    ) -> None:
        """Update the metrics line without reloading the waypoint."""
        self._waypoint_cost = cost
        self._waypoint_tokens = tokens
        metrics = self.query_one("#wp-metrics", Static)
        metrics.update(self._format_metrics_line(cost, tokens))

    def _format_metrics_line(
        self, cost: float | None, tokens: tuple[int, int] | None
    ) -> str:
        """Format the metrics line for a waypoint detail panel."""
        metrics_parts: list[str] = []
        if tokens:
            tokens_in, tokens_out = tokens
            metrics_parts.append(
                "Tokens: "
                f"{format_token_count(tokens_in)} in / "
                f"{format_token_count(tokens_out)} out"
            )
        if cost is not None and cost > 0:
            metrics_parts.append(f"Cost: ${cost:.2f}")
        return " · ".join(metrics_parts)

    def _update_output_for_waypoint(
        self, waypoint: Waypoint, active_waypoint_id: str | None
    ) -> None:
        """Update the output panel based on waypoint status."""
        log = self.execution_log

        # If this is the active waypoint, keep showing live output
        if waypoint.id == active_waypoint_id:
            # If we weren't already showing this waypoint live, clear first
            if self._showing_output_for != waypoint.id or not self._is_live_output:
                log.clear_log()
                self.clear_iteration()
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
        tool_output = entry.metadata.get("tool_output", "")

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
        if tool_name == "Bash" and isinstance(tool_input, dict):
            command = tool_input.get("command", "")
            if command:
                log.write_log(f"[dim]$ {command}[/]")
            if tool_output:
                output = tool_output.strip()
                if len(output) > 400:
                    output = output[:400] + "..."
                log.write_log(f"[dim]{output}[/]")
            return

        # Fallback for other tools
        log.write_log(f"[dim]→ {tool_name}[/]")

    def _log_soft_validation_evidence(
        self,
        log: ExecutionLog,
        receipt: "ChecklistReceipt",
        receipt_path: Path,
    ) -> None:
        """Log soft-validation evidence with command and output snippet."""
        if not receipt.soft_checklist:
            return

        log.write_log("[dim]Soft validation evidence:[/]")
        for item in receipt.soft_checklist:
            status = "[green]✓[/]" if item.status == "passed" else "[red]✗[/]"
            exit_code = item.exit_code if item.exit_code is not None else "?"
            log.write_log(f"  {status} {item.item} [dim]exit {exit_code}[/]")
            if item.command:
                log.write_log(f"    [dim]$ {item.command}[/]")
            output = (item.stdout or item.stderr or "").strip()
            if output:
                summary = output.replace("\n", " / ")
                if len(summary) > 200:
                    summary = summary[:200] + "..."
                log.write_log(f"    [dim]{summary}[/]")
            for label, rel_path in (
                ("stdout", item.stdout_path),
                ("stderr", item.stderr_path),
            ):
                if rel_path:
                    path = receipt_path.parent / rel_path
                    escaped_path = str(path).replace("'", "\\'")
                    log.write(
                        f"    [dim]{label}:[/] "
                        f"[@click=screen.preview_file('{escaped_path}')]"
                        f"[dim underline]{path}[/][/]"
                    )

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
            duration = int(
                (exec_log.completed_at - exec_log.started_at).total_seconds()
            )
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
            if result.receipt:
                detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
                detail_panel._log_soft_validation_evidence(
                    log, result.receipt, receipt_path
                )
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

    def update_project_metrics(
        self,
        cost: float,
        time_seconds: int,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        tokens_known: bool = False,
    ) -> None:
        """Update the project metrics display (cost and time).

        Args:
            cost: Total cost in USD.
            time_seconds: Total execution time in seconds.
            tokens_in: Total input tokens for the project.
            tokens_out: Total output tokens for the project.
        """
        display = _format_project_metrics(
            cost,
            time_seconds,
            tokens_in,
            tokens_out,
            tokens_known,
        )
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
        Binding("d", "debug_waypoint", "Debug", show=True),
        Binding("h", "toggle_host_validations", "HostVal", show=True),
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+f", "forward", "Forward", show=False),
        Binding("comma", "shrink_left", "< Pane", show=True),
        Binding("full_stop", "expand_left", "> Pane", show=True),
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
        self.execution_controller = ExecutionController(self.coordinator)

        self._executor: WaypointExecutor | None = None

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
        return self.execution_controller.current_waypoint

    @current_waypoint.setter
    def current_waypoint(self, waypoint: Waypoint | None) -> None:
        """Set the currently selected waypoint (delegated to coordinator)."""
        self.execution_controller.current_waypoint = waypoint

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
        # Load persisted host validation preference for this project
        self.waypoints_app.host_validations_enabled = (
            self.waypoints_app.load_host_validation_preference(self.project)
        )
        # Reflect initial state in status bar
        self._update_status_bar(self.execution_state)

        # Clean up stale IN_PROGRESS from previous sessions and select next waypoint
        self.execution_controller.initialize()

        # Update waypoint list with cost data
        self._refresh_waypoint_list()

        # Sync UI with selected waypoint (if any)
        self._sync_current_waypoint_details()

        # Sync execution state after initialization
        self.execution_state = self.execution_controller.execution_state

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
        tokens_in: int | None = None
        tokens_out: int | None = None
        tokens_known = False
        if self.waypoints_app.metrics_collector:
            cost = self.waypoints_app.metrics_collector.total_cost
            tokens_in = self.waypoints_app.metrics_collector.total_tokens_in
            tokens_out = self.waypoints_app.metrics_collector.total_tokens_out
            tokens_known = any(
                call.tokens_in is not None or call.tokens_out is not None
                for call in self.waypoints_app.metrics_collector._calls
            )
        time_seconds = self._calculate_total_execution_time()
        list_panel = self.query_one(WaypointListPanel)
        list_panel.update_project_metrics(
            cost,
            time_seconds,
            tokens_in,
            tokens_out,
            tokens_known,
        )

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
            tokens = self._get_waypoint_tokens(waypoint.id)
            detail_panel.show_waypoint(
                waypoint,
                project=self.project,
                active_waypoint_id=active_id,
                cost=cost,
                tokens=tokens,
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

    def _get_waypoint_tokens(self, waypoint_id: str) -> tuple[int, int] | None:
        """Get the token totals for a waypoint from the metrics collector."""
        if self.waypoints_app.metrics_collector:
            tokens_by_waypoint = (
                self.waypoints_app.metrics_collector.tokens_by_waypoint()
            )
            return tokens_by_waypoint.get(waypoint_id)
        return None

    def _sync_current_waypoint_details(
        self, active_waypoint_id: str | None = None
    ) -> None:
        """Sync the detail panel with the current waypoint."""
        if not self.current_waypoint:
            return

        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        cost = self._get_waypoint_cost(self.current_waypoint.id)
        tokens = self._get_waypoint_tokens(self.current_waypoint.id)
        detail_panel.show_waypoint(
            self.current_waypoint,
            project=self.project,
            active_waypoint_id=active_waypoint_id,
            cost=cost,
            tokens=tokens,
        )

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

        # Delegate selection to execution controller
        wp = self.execution_controller.select_next_waypoint(
            include_in_progress=include_in_progress
        )

        if wp:
            # Waypoint selected - update UI
            logger.info("SELECTED %s via execution controller", wp.id)
            self._sync_current_waypoint_details()
            return

        # No eligible waypoints found - sync state from controller
        self.execution_state = self.execution_controller.execution_state

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
        host_label = self._host_validation_label()
        status_bar.update(
            f"{host_label}    ⏱ {minutes}:{seconds:02d} | ${cost:.2f}    {message}"
        )

    def _update_status_bar(self, state: ExecutionState) -> None:
        """Update the status bar with state message and optional cost."""
        status_bar = self.query_one("#status-bar", Static)
        message = self._get_state_message(state)
        host_label = self._host_validation_label()

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
            status_bar.update(f"{host_label}    ${cost:.2f}    {message}")
        else:
            status_bar.update(f"{host_label}    {message}")

    def _host_validation_label(self) -> str:
        """Return a short label for host validation mode."""
        if self.waypoints_app.host_validations_enabled:
            return "HostVal: ON"
        return "HostVal: OFF (LLM-as-judge)"

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
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        selected = list_panel.selected_waypoint

        directive = self.execution_controller.start(selected)
        if directive.message:
            self.notify(directive.message)

        self.execution_state = self.execution_controller.execution_state

        if directive.action != "execute":
            return

        self._refresh_waypoint_list()
        self._sync_current_waypoint_details()
        self._execute_current_waypoint()

    def action_toggle_host_validations(self) -> None:
        """Toggle host validations for the next execution."""
        app = self.waypoints_app
        app.host_validations_enabled = not app.host_validations_enabled
        state = "ON" if app.host_validations_enabled else "OFF (LLM-as-judge only)"
        app.save_host_validation_preference(self.project)
        try:
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            detail_panel.execution_log.log_heading(f"Host validation {state}")
        except Exception:
            # Log pane may not be mounted yet
            pass
        self.notify(f"Host validation {state}")
        self.app.bell()
        logger.info("Host validations toggled to %s", state)

    def action_pause(self) -> None:
        """Pause execution after current waypoint."""
        if self.execution_controller.request_pause():
            self.execution_state = self.execution_controller.execution_state
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

    def action_debug_waypoint(self) -> None:
        """Fork a debug waypoint from the selected waypoint."""
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        selected = list_panel.selected_waypoint or self.current_waypoint
        if not selected:
            self.notify("Select a waypoint to debug", severity="warning")
            return

        def handle_result(note: str | None) -> None:
            if note is None:
                return
            debug_wp = self.coordinator.fork_debug_waypoint(selected, note)
            self._refresh_waypoint_list()
            self.current_waypoint = debug_wp
            if self.execution_state == ExecutionState.DONE:
                self.execution_state = ExecutionState.PAUSED
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            cost = self._get_waypoint_cost(debug_wp.id)
            tokens = self._get_waypoint_tokens(debug_wp.id)
            detail_panel.show_waypoint(
                debug_wp,
                project=self.project,
                active_waypoint_id=None,
                cost=cost,
                tokens=tokens,
            )
            self.notify(f"Debug waypoint created: {debug_wp.id}")

        self.app.push_screen(DebugWaypointModal(), handle_result)

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
            self.coordinator.transition(JourneyState.CHART_REVIEW)

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

    def _switch_to_land_screen(self) -> None:
        """Switch to the Land screen after all waypoints complete."""
        self.waypoints_app.switch_phase(
            "land",
            {
                "project": self.project,
                "flight_plan": self.flight_plan,
                "spec": self.spec,
            },
        )

    def _execute_current_waypoint(self) -> None:
        """Execute the current waypoint using agentic AI."""
        if not self.current_waypoint:
            return

        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        # Mark this as the active waypoint for output tracking
        detail_panel._showing_output_for = self.current_waypoint.id

        log.clear_log()
        wp_title = f"{self.current_waypoint.id}: {self.current_waypoint.title}"
        log.log_heading(f"Starting {wp_title}")
        host_state = (
            "ON"
            if self.waypoints_app.host_validations_enabled
            else "OFF (LLM-as-judge only)"
        )
        log.log_success(f"Host validation: {host_state}")
        detail_panel.clear_iteration()

        # Refresh the waypoint list to show blinking status
        self._refresh_waypoint_list()

        # Calculate max iterations (default + any additional from retry)
        from waypoints.fly.executor import MAX_ITERATIONS

        # Create executor with progress callback
        self._executor = self.execution_controller.build_executor(
            waypoint=self.current_waypoint,
            spec=self.spec,
            on_progress=self._on_execution_progress,
            max_iterations=MAX_ITERATIONS,
            metrics_collector=self.waypoints_app.metrics_collector,
            host_validations_enabled=self.waypoints_app.host_validations_enabled,
        )

        # Run execution in background worker
        self.run_worker(
            self._run_executor(),
            name="waypoint_executor",
            exclusive=True,
            thread=True,
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
        elif ctx.step == "stage":
            log.log_heading(f"Stage: {ctx.output}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker completion."""
        if event.worker.name != "waypoint_executor":
            return

        if event.worker.is_finished:
            # Check for InterventionNeededError exception
            if event.worker.state.name == "ERROR":
                # Worker raised an exception - check if it's an intervention
                try:
                    # Accessing result will re-raise WorkerFailed wrapping original
                    _ = event.worker.result
                except WorkerFailed as wf:
                    # Extract the original exception from WorkerFailed wrapper
                    original = wf.error
                    if isinstance(original, InterventionNeededError):
                        self._handle_intervention(original.intervention)
                        return
                    # Other exception - treat as failure
                    logger.exception("Worker failed with exception: %s", original)
                    self._handle_execution_result(ExecutionResult.FAILED)
                    return
                except Exception as e:
                    # Fallback for any other exception type
                    logger.exception("Worker failed with exception: %s", e)
                    self._handle_execution_result(ExecutionResult.FAILED)
                    return

            result = event.worker.result
            self._handle_execution_result(result)

    def _handle_execution_result(self, result: ExecutionResult | None) -> None:
        """Handle the result of waypoint execution."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        completed_waypoint = self.current_waypoint

        # Update header cost display after execution
        self.waypoints_app.update_header_cost()

        # Update project metrics (cost and time) after execution
        self._update_project_metrics()

        if completed_waypoint:
            cost = self._get_waypoint_cost(completed_waypoint.id)
            tokens = self._get_waypoint_tokens(completed_waypoint.id)
            detail_panel.update_metrics(cost, tokens)

        directive = self.execution_controller.handle_execution_result(result)
        self.execution_state = self.execution_controller.execution_state

        if directive.completed:
            log.log_success(f"Waypoint {directive.completed.id} complete!")

            self._live_criteria_completed = ExecutionLogReader.get_completed_criteria(
                self.project,
                directive.completed.id,
            )

            # Show verification summary
            self._log_verification_summary(directive.completed, log)

            # Commit waypoint completion (validates receipt first)
            commit_outcome = self.coordinator.commit_waypoint(directive.completed)
            for notice in commit_outcome.notices:
                if notice.severity == "info":
                    self.notify(notice.message)
                else:
                    self.notify(notice.message, severity=notice.severity)
            if self._executor and self._executor._log_writer:
                if commit_outcome.status == "success":
                    self._executor._log_writer.log_git_commit(
                        True,
                        commit_outcome.commit_hash or "",
                        commit_outcome.commit_msg or "",
                    )
                elif commit_outcome.status == "failure":
                    self._executor._log_writer.log_git_commit(
                        False,
                        "",
                        commit_outcome.message or "Commit failed",
                    )

            # Reset live criteria tracking for next waypoint
            self._live_criteria_completed = set()

            detail_panel.clear_iteration()
            self._refresh_waypoint_list()

        if directive.action == "execute":
            self._sync_current_waypoint_details()
            self._execute_current_waypoint()
            return

        if directive.action == "land":
            self._switch_to_land_screen()
            return

        if directive.action == "intervention":
            log.log_error(directive.message or "Human intervention needed")
            self.query_one(StatusHeader).set_error()
            if directive.message:
                self.notify(directive.message, severity="warning")
            self._refresh_waypoint_list()
            return

        if directive.action == "pause":
            if result == ExecutionResult.CANCELLED:
                log.write_log("Execution cancelled")
            self._refresh_waypoint_list()
            return

    def _handle_intervention(self, intervention: Intervention) -> None:
        """Handle an intervention request by showing the modal."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        # Log the intervention
        type_label = intervention.type.value.replace("_", " ").title()
        log.log_error(f"Intervention needed: {type_label}")
        log.write_log(intervention.error_summary[:500])

        # Record the intervention and update state
        self.execution_controller.prepare_intervention(intervention)
        self.execution_state = self.execution_controller.execution_state
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
            log.write_log(
                f"Retrying with {result.additional_iterations} additional iterations"
            )
        elif result.action == InterventionAction.SKIP:
            log.write_log("Skipping waypoint")
        elif result.action == InterventionAction.EDIT:
            log.write_log("Edit waypoint requested")
            self.notify(
                "Edit waypoint in flight plan, then press 'r' to retry",
                severity="information",
            )
        elif result.action == InterventionAction.ROLLBACK:
            log.write_log("Rolling back to last safe tag")
            self._rollback_to_safe_tag(result.rollback_tag)
        elif result.action == InterventionAction.ABORT:
            log.write_log("Execution aborted")
            self.notify("Execution aborted")

        directive = self.execution_controller.resolve_intervention(result)
        if directive.message:
            self.notify(directive.message)
        self.execution_state = self.execution_controller.execution_state
        self.query_one(StatusHeader).set_normal()

        self._refresh_waypoint_list()

        if directive.action == "execute":
            self._sync_current_waypoint_details()
            self._execute_current_waypoint()
        elif directive.action == "land":
            self.notify("All waypoints complete!")
            self._switch_to_land_screen()

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
        """Save the flight plan to disk via coordinator."""
        self.coordinator.save_flight_plan()

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
            if result.receipt:
                detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
                detail_panel._log_soft_validation_evidence(
                    log, result.receipt, receipt_path
                )
        else:
            log.write_log("[yellow]⚠ No receipt found[/]")

    def _check_parent_completion(self, completed_waypoint: Waypoint) -> None:
        """Check if parent epic is ready for execution.

        Delegates to coordinator. Note: parents are no longer auto-completed;
        they will be selected and executed to verify their acceptance criteria.

        Args:
            completed_waypoint: The waypoint that just completed
        """
        # Delegate to coordinator - it logs readiness but doesn't auto-complete
        self.coordinator.check_parent_completion(completed_waypoint)

    def action_forward(self) -> None:
        """Go forward to Land screen if available."""
        # Check if Land is available (all waypoints complete or already in LAND_REVIEW)
        journey = self.project.journey
        if journey and journey.state == JourneyState.LAND_REVIEW:
            self._switch_to_land_screen()
            return

        # Check if all waypoints are complete
        all_complete, pending, failed, blocked = self._get_completion_status()
        if all_complete:
            self.coordinator.transition(JourneyState.LAND_REVIEW)
            self._switch_to_land_screen()
        elif self.execution_state == ExecutionState.DONE:
            # DONE but not all_complete - blocked waypoints
            self.notify("Cannot land yet - some waypoints are blocked or failed")
        else:
            self.notify("Cannot land yet - waypoints still in progress")

    def action_shrink_left(self) -> None:
        """Shrink the left pane."""
        split = self.query_one(ResizableSplit)
        split.left_pct = max(15, split.left_pct - 5)

    def action_expand_left(self) -> None:
        """Expand the left pane."""
        split = self.query_one(ResizableSplit)
        split.left_pct = min(70, split.left_pct + 5)
