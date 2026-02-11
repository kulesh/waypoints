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
    from waypoints.fly.types import ExecutionContext
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

    WaypointDetailPanel .wp-agent-monitor {
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
        self._agent_status: dict[str, str] = {
            "orchestrator": "Waiting for execution",
            "builder": "Idle",
            "verifier": "Idle",
        }
        self._orchestrator_expectations: tuple[str, ...] = ()
        self._orchestrator_stop_conditions: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel-header"):
            yield Static("Select a waypoint", classes="wp-title", id="wp-title")
            yield Static("", classes="wp-objective", id="wp-objective")
            yield Static("", classes="wp-notes", id="wp-notes")
            yield Static("Status: Pending", classes="wp-status", id="wp-status")
            yield Static("", classes="wp-metrics", id="wp-metrics")
            yield Static("", classes="wp-log-view", id="wp-log-view")
            yield Static("", classes="wp-status", id="iteration-label")
            yield Static("", classes="wp-agent-monitor", id="wp-agent-monitor")
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
        self._reset_agent_monitor()

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
            self._set_agent_status("orchestrator", "Select a waypoint to begin")
            self._set_agent_status("builder", "Idle")
            self._set_agent_status("verifier", "Idle")

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

    def _reset_agent_monitor(self) -> None:
        """Reset agent monitor state for a newly selected waypoint."""
        self._agent_status = {
            "orchestrator": "Supervising waypoint scope and quality gates",
            "builder": "Waiting for run",
            "verifier": "Waiting for completion claim",
        }
        self._orchestrator_expectations = ()
        self._orchestrator_stop_conditions = ()
        self._render_agent_monitor()

    def _set_agent_status(self, role: str, status: str) -> None:
        """Set short status text for one role and re-render monitor."""
        key = role.lower()
        if key not in self._agent_status:
            return
        self._agent_status[key] = self._truncate_agent_text(status, limit=52)
        self._render_agent_monitor()

    def _set_orchestrator_expectations(
        self,
        constraints: tuple[str, ...],
        stop_conditions: tuple[str, ...],
    ) -> None:
        """Store orchestrator constraints propagated to worker agents."""
        self._orchestrator_expectations = constraints[:2]
        self._orchestrator_stop_conditions = stop_conditions[:2]
        if constraints or stop_conditions:
            self._set_agent_status(
                "orchestrator",
                "Guidance updated for builder/verifier",
            )
        self._render_agent_monitor()

    def _truncate_agent_text(self, text: str, *, limit: int) -> str:
        """Return compact monitor text clipped to a fixed width."""
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."

    def _render_agent_monitor(self) -> None:
        """Render persistent multi-agent activity summary."""
        try:
            monitor = self.query_one("#wp-agent-monitor", Static)
        except Exception:
            return

        text = Text()
        text.append("Agent Monitor · ", style="bold")
        text.append("O: ", style="bold")
        text.append(self._agent_status["orchestrator"], style="dim")
        text.append(" · ", style="dim")
        text.append("B: ", style="bold")
        text.append(self._agent_status["builder"], style="dim")
        text.append(" · ", style="dim")
        text.append("V: ", style="bold")
        text.append(self._agent_status["verifier"], style="dim")
        text.append("\n")

        if self._orchestrator_expectations:
            text.append("Expect: ", style="bold")
            text.append(
                self._truncate_agent_text(
                    " | ".join(self._orchestrator_expectations),
                    limit=160,
                )
                + "\n",
                style="dim",
            )

        if self._orchestrator_stop_conditions:
            text.append("Stops: ", style="bold")
            text.append(
                self._truncate_agent_text(
                    " | ".join(self._orchestrator_stop_conditions),
                    limit=160,
                ),
                style="dim",
            )

        monitor.update(text)

    def apply_agent_progress(self, ctx: "ExecutionContext") -> None:
        """Update multi-agent monitor state from live execution progress."""
        if ctx.step == "protocol_artifact":
            artifact = ctx.metadata.get("artifact")
            if isinstance(artifact, dict):
                self._apply_protocol_artifact_to_monitor(artifact)
            return

        role = self._derive_role_from_progress(ctx)
        output = self._truncate_agent_text(ctx.output, limit=92)

        if ctx.step == "executing":
            self._set_agent_status(role, f"Running iteration {ctx.iteration}")
        elif ctx.step == "stage":
            self._set_agent_status(role, f"Stage update: {output}")
        elif ctx.step == "tool_use":
            self._set_agent_status(role, f"Using tool: {output}")
        elif ctx.step == "finalizing":
            self._set_agent_status("verifier", output)
        elif ctx.step == "complete":
            self._set_agent_status("verifier", "Receipt accepted")
            self._set_agent_status("orchestrator", "Accepted completion claim")
        elif ctx.step == "validation_failed":
            self._set_agent_status("verifier", "Rejected receipt evidence")
            self._set_agent_status("orchestrator", "Requested rework and retry")
        elif ctx.step == "clarification_pending":
            self._set_agent_status(
                "orchestrator",
                "Blocked completion until clarification is resolved",
            )
        elif ctx.step in {"error", "warning"}:
            self._set_agent_status("orchestrator", output)

    def _derive_role_from_progress(self, ctx: "ExecutionContext") -> str:
        """Map progress update metadata/step to one canonical role label."""
        raw_role = ctx.metadata.get("role")
        if isinstance(raw_role, str) and raw_role:
            return raw_role
        if ctx.step in {"finalizing"}:
            return "verifier"
        if ctx.step in {"complete", "validation_failed", "clarification_pending"}:
            return "orchestrator"
        return "builder"

    def _apply_protocol_artifact_to_monitor(self, artifact: dict[str, Any]) -> None:
        """Update monitor status lines from structured protocol artifacts."""
        artifact_type = str(artifact.get("artifact_type", "")).strip()
        role = str(artifact.get("produced_by_role", "orchestrator")).strip() or (
            "orchestrator"
        )

        if artifact_type == "guidance_packet":
            constraints = tuple(
                str(item) for item in artifact.get("role_constraints", []) if item
            )
            stops = tuple(
                str(item) for item in artifact.get("stop_conditions", []) if item
            )
            self._set_orchestrator_expectations(constraints, stops)
            self._set_agent_status(role, "Received orchestrator guidance")
            return

        if artifact_type == "clarification_request":
            question = str(artifact.get("blocking_question", "")).strip()
            self._set_agent_status("builder", f"Needs clarification: {question}")
            self._set_agent_status("orchestrator", "Reviewing clarification request")
            return

        if artifact_type == "clarification_response":
            option = str(artifact.get("chosen_option", "")).strip()
            self._set_agent_status(
                "orchestrator",
                f"Clarification resolved: {option}",
            )
            self._set_agent_status("builder", "Resuming with clarified constraints")
            return

        if artifact_type == "verification_report":
            results = artifact.get("criteria_results", [])
            passed = 0
            failed = 0
            if isinstance(results, list):
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    verdict = str(item.get("verdict", "")).strip().lower()
                    if verdict == "pass":
                        passed += 1
                    elif verdict == "fail":
                        failed += 1
            self._set_agent_status(
                "verifier",
                f"Reported {passed} pass / {failed} fail criteria verdicts",
            )
            return

        if artifact_type == "orchestrator_decision":
            disposition = str(artifact.get("disposition", "escalate")).strip()
            self._set_agent_status(
                "orchestrator",
                f"Decision: {disposition}",
            )
            return

        self._set_agent_status(role, f"Emitted {artifact_type or 'protocol artifact'}")

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

    def set_live_output_waypoint(self, waypoint_id: str | None) -> None:
        """Mark which waypoint's execution output should be treated as live."""
        self._showing_output_for = waypoint_id
        self._is_live_output = waypoint_id is not None
        if waypoint_id is not None:
            self._set_agent_status(
                "orchestrator",
                "Supervising builder execution and completion quality gate",
            )
            self._set_agent_status("builder", "Preparing first iteration")
            self._set_agent_status("verifier", "Waiting for completion claim")

    def is_showing_output_for(self, waypoint_id: str) -> bool:
        """Return whether output for the given waypoint is currently displayed."""
        return self._showing_output_for == waypoint_id

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
        elif cost is not None or cached_tokens_in is not None:
            metrics_parts.append("Tokens: unavailable")
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
            self._set_agent_status(
                "orchestrator",
                "Supervising builder execution and completion quality gate",
            )
            self._set_agent_status("builder", "Executing waypoint tasks")
            self._set_agent_status("verifier", "Waiting for completion claim")
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
            self._set_agent_status("orchestrator", "Execution already complete")
            self._set_agent_status("builder", "Completed")
            self._set_agent_status("verifier", "Completed")
        elif waypoint.status == WaypointStatus.FAILED:
            log.log_error("Last execution failed")
            log.write_log("Press 'r' to retry")
            log.write_log("(No execution log found)")
            self._set_agent_status("orchestrator", "Awaiting retry decision")
            self._set_agent_status("builder", "Last run failed")
            self._set_agent_status("verifier", "Did not accept completion")
        elif waypoint.status == WaypointStatus.IN_PROGRESS:
            # In progress but not active (stale from previous session)
            log.write_log("Execution was in progress...")
            log.write_log("(Session may have been interrupted)")
            self._set_agent_status("orchestrator", "Execution interrupted")
            self._set_agent_status("builder", "State unknown (stale in-progress)")
        else:  # PENDING
            log.write_log("Waiting to execute")
            if waypoint.dependencies:
                deps = ", ".join(waypoint.dependencies)
                log.write_log(f"Dependencies: {deps}")
            self._set_agent_status("builder", "Pending")
            self._set_agent_status("verifier", "Pending")

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
            self._replay_agent_activity(exec_log)
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
                tokens_in = entry.metadata.get("tokens_in")
                tokens_out = entry.metadata.get("tokens_out")
                cached_tokens_in = entry.metadata.get("cached_tokens_in")
                parts: list[str] = []
                if isinstance(cost, (int, float)) and cost > 0:
                    parts.append(f"cost=${cost:.4f}")
                if isinstance(tokens_in, int) or isinstance(tokens_out, int):
                    parts.append(
                        "tokens="
                        f"{format_token_count(int(tokens_in or 0))} in / "
                        f"{format_token_count(int(tokens_out or 0))} out"
                    )
                if isinstance(cached_tokens_in, int):
                    parts.append(f"cached={format_token_count(cached_tokens_in)} in")
                if parts:
                    log.write_log(f"(Iteration {' | '.join(parts)})")

            elif entry_type == "tool_call":
                self._log_tool_call_entry(log, entry)

            elif entry_type == "stage_report":
                self._log_stage_report_entry(log, entry)

            elif entry_type == "protocol_artifact":
                self._log_protocol_artifact_entry(log, entry)

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

            elif entry_type == "finalize_end":
                cost = entry.metadata.get("cost_usd")
                tokens_in = entry.metadata.get("tokens_in")
                tokens_out = entry.metadata.get("tokens_out")
                cached_tokens_in = entry.metadata.get("cached_tokens_in")
                verifier_parts: list[str] = []
                if isinstance(cost, (int, float)) and cost > 0:
                    verifier_parts.append(f"cost=${cost:.4f}")
                if isinstance(tokens_in, int) or isinstance(tokens_out, int):
                    verifier_parts.append(
                        "tokens="
                        f"{format_token_count(int(tokens_in or 0))} in / "
                        f"{format_token_count(int(tokens_out or 0))} out"
                    )
                if isinstance(cached_tokens_in, int):
                    verifier_parts.append(
                        f"cached={format_token_count(cached_tokens_in)} in"
                    )
                if verifier_parts:
                    log.write_log(f"(Verifier {' | '.join(verifier_parts)})")

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

            elif entry_type == "finalize_start":
                log.log_heading("Verifier")
                log.write_log("[dim]Running receipt finalization[/]")

            elif entry_type == "finalize_output":
                if entry.content:
                    content = entry.content
                    if len(content) > 24000:
                        content = content[:24000] + "\n... (truncated)"
                    log.write_log(content)

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

    def _log_stage_report_entry(self, log: "ExecutionLog", entry: Any) -> None:
        """Log a structured stage report emitted by the builder agent."""
        stage = str(entry.metadata.get("stage", "unknown")).strip() or "unknown"
        success = bool(entry.metadata.get("success", False))
        output = str(entry.metadata.get("output", "")).strip()
        next_stage = str(entry.metadata.get("next_stage", "")).strip()
        status = "[green]✓[/]" if success else "[yellow]~[/]"
        log.write_log(f"{status} [cyan]Builder[/] stage [bold]{stage}[/]")
        if output:
            log.write_log(f"[dim]  {self._truncate_agent_text(output, limit=220)}[/]")
        if next_stage:
            log.write_log(f"[dim]  next: {next_stage}[/]")
        stage_summary = output or ("ok" if success else "needs attention")
        self._set_agent_status("builder", f"Stage {stage}: {stage_summary}")

    def _log_protocol_artifact_entry(self, log: "ExecutionLog", entry: Any) -> None:
        """Log a protocol artifact in human-readable form."""
        artifact = entry.metadata.get("artifact")
        if not isinstance(artifact, dict):
            log.write_log("[dim]Protocol artifact emitted[/]")
            return
        role = str(artifact.get("produced_by_role", "orchestrator")).strip()
        summary = self._format_protocol_artifact_summary(artifact)
        log.write_log(f"[bold blue]{role}[/] · {summary}")
        self._apply_protocol_artifact_to_monitor(artifact)

    def _format_protocol_artifact_summary(self, artifact: dict[str, Any]) -> str:
        """Render concise summary text for one protocol artifact payload."""
        artifact_type = str(artifact.get("artifact_type", "protocol_artifact")).strip()

        if artifact_type == "guidance_packet":
            constraints = artifact.get("role_constraints", [])
            stops = artifact.get("stop_conditions", [])
            skills = artifact.get("attached_skills", [])
            constraints_count = len(constraints) if isinstance(constraints, list) else 0
            stops_count = len(stops) if isinstance(stops, list) else 0
            skills_count = len(skills) if isinstance(skills, list) else 0
            return (
                "Guidance packet "
                f"({constraints_count} constraints, {stops_count} stops, "
                f"{skills_count} skills)"
            )

        if artifact_type == "context_envelope":
            slices = artifact.get("slices", [])
            slices_count = len(slices) if isinstance(slices, list) else 0
            budget = artifact.get("prompt_budget_chars")
            overflowed = bool(artifact.get("overflowed", False))
            return (
                f"Context envelope budget={budget} chars, slices={slices_count}, "
                f"overflowed={'yes' if overflowed else 'no'}"
            )

        if artifact_type == "clarification_request":
            question = str(artifact.get("blocking_question", "")).strip()
            return "Clarification requested: " + self._truncate_agent_text(
                question or "unspecified question",
                limit=140,
            )

        if artifact_type == "clarification_response":
            option = str(artifact.get("chosen_option", "")).strip()
            return "Clarification response: " + self._truncate_agent_text(
                option or "follow orchestrator guidance",
                limit=140,
            )

        if artifact_type == "verification_report":
            raw_results = artifact.get("criteria_results", [])
            passed = 0
            failed = 0
            inconclusive = 0
            if isinstance(raw_results, list):
                for item in raw_results:
                    if not isinstance(item, dict):
                        continue
                    verdict = str(item.get("verdict", "")).strip().lower()
                    if verdict == "pass":
                        passed += 1
                    elif verdict == "fail":
                        failed += 1
                    elif verdict == "inconclusive":
                        inconclusive += 1
            return (
                "Verification report: "
                f"{passed} pass / {failed} fail / {inconclusive} inconclusive"
            )

        if artifact_type == "orchestrator_decision":
            disposition = str(artifact.get("disposition", "escalate")).strip()
            reason = str(artifact.get("reason_code", "")).strip()
            reason_text = f", reason={reason}" if reason else ""
            return f"Orchestrator decision: {disposition}{reason_text}"

        return artifact_type

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

    def log_soft_validation_evidence(
        self,
        log: ExecutionLog,
        receipt: "ChecklistReceipt",
        receipt_path: Path,
    ) -> None:
        """Public wrapper for logging soft validation evidence."""
        self._log_soft_validation_evidence(log, receipt, receipt_path)

    def _replay_agent_activity(self, exec_log: "ExecLogType") -> None:
        """Reconstruct agent monitor state from persisted execution entries."""
        for entry in exec_log.entries:
            if entry.entry_type == "protocol_artifact":
                artifact = entry.metadata.get("artifact")
                if isinstance(artifact, dict):
                    self._apply_protocol_artifact_to_monitor(artifact)
                continue

            if entry.entry_type == "stage_report":
                stage = str(entry.metadata.get("stage", "")).strip()
                output = str(entry.metadata.get("output", "")).strip()
                if stage:
                    summary = output or "reported"
                    self._set_agent_status("builder", f"Stage {stage}: {summary}")
                continue

            if entry.entry_type == "finalize_start":
                self._set_agent_status(
                    "verifier",
                    "Running receipt finalization and criterion checks",
                )
                continue

            if entry.entry_type == "receipt_validated":
                valid = bool(entry.metadata.get("valid", False))
                if valid:
                    self._set_agent_status("verifier", "Receipt accepted")
                    self._set_agent_status(
                        "orchestrator", "Accepted waypoint completion"
                    )
                else:
                    self._set_agent_status("verifier", "Receipt rejected")
                    self._set_agent_status("orchestrator", "Requested rework")
                continue

            if entry.entry_type == "intervention_needed":
                self._set_agent_status("orchestrator", "Intervention required")
                continue

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
                self.log_soft_validation_evidence(log, result.receipt, receipt_path)
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
        self._set_agent_status("orchestrator", "Supervising multi-hop child waypoints")
        self._set_agent_status("builder", "No direct execution on epic parent")
        self._set_agent_status("verifier", "Evaluates child-waypoint receipts")

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
