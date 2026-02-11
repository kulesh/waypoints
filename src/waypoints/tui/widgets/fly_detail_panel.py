"""Detail panel widgets for Fly screen."""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from waypoints.fly.execution_log import (
    ExecutionLog as ExecLogType,
)
from waypoints.fly.execution_log import (
    ExecutionLogReader,
)
from waypoints.git import ReceiptValidator
from waypoints.models import Project
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.utils import (
    format_token_count,
    get_status_color,
    get_status_icon,
    get_status_label,
    get_status_markup,
)
from waypoints.tui.widgets.fly_execution_log import ExecutionLog
from waypoints.tui.widgets.resizable_split import ResizableSplitVertical

if TYPE_CHECKING:
    from waypoints.git.receipt import ChecklistReceipt

logger = logging.getLogger(__name__)


class ExecutionLogViewMode(Enum):
    """Rendering mode for execution history."""

    RAW = "raw"
    SUMMARY = "summary"


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

    WaypointDetailPanel .wp-log-view {
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
        self._waypoint_cached_tokens_in: int | None = None
        self._showing_output_for: str | None = None  # Track which waypoint's output
        self._is_live_output: bool = False  # True if showing live streaming output
        self._log_view_mode: ExecutionLogViewMode = ExecutionLogViewMode.RAW

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("Select a waypoint", classes="wp-title", id="wp-title")
            yield Static("", classes="wp-objective", id="wp-objective")
            yield Static("", classes="wp-notes", id="wp-notes")
            yield Static("Status: Pending", classes="wp-status", id="wp-status")
            yield Static("", classes="wp-metrics", id="wp-metrics")
            yield Static("", classes="wp-log-view", id="wp-log-view")
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
        cached_tokens_in: int | None = None,
        force_reload: bool = False,
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
        self._waypoint_cached_tokens_in = cached_tokens_in

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

        self._update_log_view_label()

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
            metrics.update(self._format_metrics_line(cost, tokens, cached_tokens_in))

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
            self._update_output_for_waypoint(
                waypoint,
                active_waypoint_id,
                force_reload=force_reload,
            )
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

    def _update_log_view_label(self) -> None:
        """Render the current execution log view mode."""
        label = self.query_one("#wp-log-view", Static)
        mode = "Raw" if self._log_view_mode == ExecutionLogViewMode.RAW else "Summary"
        label.update(f"Log View: {mode} (x to toggle)")

    @property
    def log_view_mode(self) -> ExecutionLogViewMode:
        """Current log rendering mode."""
        return self._log_view_mode

    def toggle_log_view_mode(self) -> ExecutionLogViewMode:
        """Toggle between raw and summary log rendering modes."""
        self._log_view_mode = (
            ExecutionLogViewMode.SUMMARY
            if self._log_view_mode == ExecutionLogViewMode.RAW
            else ExecutionLogViewMode.RAW
        )
        self._update_log_view_label()
        return self._log_view_mode

    def refresh_current_waypoint(self, active_waypoint_id: str | None = None) -> None:
        """Force-refresh currently displayed waypoint details."""
        self.show_waypoint(
            self._waypoint,
            project=self._project,
            active_waypoint_id=active_waypoint_id,
            cost=self._waypoint_cost,
            tokens=self._waypoint_tokens,
            cached_tokens_in=self._waypoint_cached_tokens_in,
            force_reload=True,
        )

    def update_metrics(
        self,
        cost: float | None,
        tokens: tuple[int, int] | None,
        cached_tokens_in: int | None,
    ) -> None:
        """Update the metrics line without reloading the waypoint."""
        self._waypoint_cost = cost
        self._waypoint_tokens = tokens
        self._waypoint_cached_tokens_in = cached_tokens_in
        metrics = self.query_one("#wp-metrics", Static)
        metrics.update(self._format_metrics_line(cost, tokens, cached_tokens_in))

    def _format_metrics_line(
        self,
        cost: float | None,
        tokens: tuple[int, int] | None,
        cached_tokens_in: int | None,
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
        if cached_tokens_in is not None:
            metrics_parts.append(f"Cached: {format_token_count(cached_tokens_in)} in")
        if cost is not None and cost > 0:
            metrics_parts.append(f"Cost: ${cost:.2f}")
        return " · ".join(metrics_parts)

    def _update_output_for_waypoint(
        self,
        waypoint: Waypoint,
        active_waypoint_id: str | None,
        *,
        force_reload: bool = False,
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
        if (
            not force_reload
            and self._showing_output_for == waypoint.id
            and not self._is_live_output
        ):
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
        """Log execution entries and return max iteration count."""
        if self._log_view_mode == ExecutionLogViewMode.RAW:
            return self._log_execution_entries_raw(log, exec_log)
        return self._log_execution_entries_summary(log, exec_log)

    def _log_execution_entries_summary(
        self, log: "ExecutionLog", exec_log: "ExecLogType"
    ) -> int:
        """Log summarized execution entries and return max iteration count."""
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

            elif entry_type == "workspace_diff":
                self._log_workspace_diff_entry(log, entry)

        return max_iteration

    def _log_execution_entries_raw(
        self, log: "ExecutionLog", exec_log: "ExecLogType"
    ) -> int:
        """Log raw JSON payloads for every execution entry."""
        max_iteration = 0

        for entry in exec_log.entries:
            max_iteration = max(max_iteration, entry.iteration)
            timestamp = entry.timestamp.strftime("%H:%M:%S")
            iteration = f"iter {entry.iteration}" if entry.iteration > 0 else "iter -"
            log.write_log(
                f"[cyan]{timestamp}[/] [dim]{iteration}[/] [bold]{entry.entry_type}[/]"
            )
            payload = self._build_raw_entry_payload(entry)
            rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
            log.write(
                Syntax(
                    rendered,
                    "json",
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True,
                )
            )
            log.write_log("")

        return max_iteration

    def _build_raw_entry_payload(self, entry: Any) -> dict[str, Any]:
        """Build stable JSON payload for raw log rendering."""
        payload: dict[str, Any] = {}
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        payload.update(metadata)

        payload.setdefault("type", entry.entry_type)
        payload.setdefault("timestamp", entry.timestamp.isoformat())
        if entry.iteration > 0:
            payload.setdefault("iteration", entry.iteration)

        if entry.entry_type == "error":
            if entry.content and "error" not in payload:
                payload["error"] = entry.content
            return payload

        if (
            entry.content
            and "content" not in payload
            and "prompt" not in payload
            and "reason" not in payload
        ):
            payload["content"] = entry.content

        return payload

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
        if tool_name == "ValidationCommand" and isinstance(tool_input, dict):
            command = str(tool_input.get("command", "")).strip()
            category = str(tool_input.get("category", "")).strip() or "validation"
            attempts = tool_input.get("attempts")
            timed_out = bool(tool_input.get("timed_out", False))
            timeout_seconds = tool_input.get("timeout_seconds")
            signals = tool_input.get("signals", [])
            timeout_events = tool_input.get("timeout_events", [])

            if command:
                log.write_log(f"[dim]$ {command}[/]")
            summary_bits = [f"category={category}"]
            if isinstance(attempts, int):
                summary_bits.append(f"attempts={attempts}")
            if timeout_seconds is not None:
                summary_bits.append(f"budget={timeout_seconds}s")
            summary_bits.append(f"timed_out={'yes' if timed_out else 'no'}")
            log.write_log(f"[dim]  {' · '.join(summary_bits)}[/]")

            if isinstance(signals, list) and signals:
                rendered_signals = " -> ".join(str(item) for item in signals)
                log.write_log(f"[dim]  signals: {rendered_signals}[/]")

            if isinstance(timeout_events, list):
                for raw_event in timeout_events:
                    if not isinstance(raw_event, dict):
                        continue
                    event_type = str(raw_event.get("event_type", "event"))
                    attempt = raw_event.get("attempt")
                    budget = raw_event.get("timeout_seconds")
                    detail = str(raw_event.get("detail", "")).strip()
                    bits = [event_type]
                    if attempt is not None:
                        bits.append(f"attempt={attempt}")
                    if budget is not None:
                        bits.append(f"budget={budget}s")
                    if detail:
                        bits.append(detail)
                    log.write_log(f"[yellow]  timeout: {' · '.join(bits)}[/]")

            if isinstance(tool_output, str) and tool_output.strip():
                output = tool_output.strip()
                if len(output) > 400:
                    output = output[:400] + "..."
                log.write_log(f"[dim]  output: {output}[/]")
            return

        # Fallback for other tools
        log.write_log(f"[dim]→ {tool_name}[/]")
        if tool_input:
            tool_input_text = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
            if len(tool_input_text) > 400:
                tool_input_text = tool_input_text[:400] + "..."
            log.write_log(f"[dim]  input: {tool_input_text}[/]")
        if isinstance(tool_output, str) and tool_output.strip():
            output = tool_output.strip()
            if len(output) > 400:
                output = output[:400] + "..."
            log.write_log(f"[dim]  output: {output}[/]")

    def _log_workspace_diff_entry(self, log: "ExecutionLog", entry: Any) -> None:
        """Log workspace provenance summary from before/after snapshots."""
        total = int(entry.metadata.get("total_files_changed", 0))
        if total == 0:
            log.write_log("[dim]Δ Workspace: no file changes detected[/]")
            return

        files_added = int(entry.metadata.get("files_added", 0))
        files_modified = int(entry.metadata.get("files_modified", 0))
        files_deleted = int(entry.metadata.get("files_deleted", 0))
        approx_tokens = int(entry.metadata.get("approx_tokens_changed", 0))
        indeterminate = int(entry.metadata.get("indeterminate_text_files", 0))

        summary = (
            f"[dim]Δ Workspace: {total} files "
            f"(+{files_added} ~{files_modified} -{files_deleted}) · "
            f"~{format_token_count(approx_tokens)} tokens from text deltas[/]"
        )
        log.write_log(summary)
        if indeterminate > 0:
            log.write_log(
                "[dim]  "
                f"{indeterminate} changed text file(s) had size-only estimates"
                "[/]"
            )

        top = entry.metadata.get("top_changed_files", [])
        if isinstance(top, list):
            for item in top[:5]:
                if not isinstance(item, dict):
                    continue
                path = item.get("path")
                if not isinstance(path, str) or not path:
                    continue
                change_type = item.get("change_type", "modified")
                added_chars = int(item.get("text_chars_added", 0) or 0)
                removed_chars = int(item.get("text_chars_removed", 0) or 0)
                if change_type == "added":
                    icon = "+"
                elif change_type == "deleted":
                    icon = "-"
                else:
                    icon = "~"
                log.write_log(
                    "  [dim]"
                    f"{icon} {path} "
                    f"(+{added_chars} chars, -{removed_chars} chars)"
                    "[/]"
                )

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
