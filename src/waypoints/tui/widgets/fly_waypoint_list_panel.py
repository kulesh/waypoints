"""Waypoint list panel widget for Fly screen."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.tui.utils import format_token_count
from waypoints.tui.widgets.flight_plan import FlightPlanTree


def format_project_metrics(
    cost: float,
    time_seconds: int,
    tokens_in: int | None,
    tokens_out: int | None,
    tokens_known: bool,
    cached_tokens_in: int | None,
    cached_tokens_known: bool,
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
    if cached_tokens_known or cached_tokens_in:
        parts.append(f"Cached: {format_token_count(cached_tokens_in or 0)} in")
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


_format_project_metrics = format_project_metrics


def _state_name(state: Any) -> str:
    if isinstance(state, str):
        return state
    return str(getattr(state, "value", state))


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
        self._execution_state: str = "idle"
        self._blink_visible: bool = True
        self._blink_timer: object = None

    def on_mount(self) -> None:
        """Start the blink timer."""
        self._blink_timer = self.set_interval(0.5, self._toggle_blink)

    def _toggle_blink(self) -> None:
        """Toggle blink state for running indicator."""
        # Blink for both RUNNING and PAUSE_PENDING (still executing)
        if self._execution_state in ("running", "pause_pending"):
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
        cached_tokens_in: int | None = None,
        cached_tokens_known: bool = False,
    ) -> None:
        """Update the project metrics display (cost and time).

        Args:
            cost: Total cost in USD.
            time_seconds: Total execution time in seconds.
            tokens_in: Total input tokens for the project.
            tokens_out: Total output tokens for the project.
            cached_tokens_in: Total cached input tokens for the project.
        """
        display = format_project_metrics(
            cost,
            time_seconds,
            tokens_in,
            tokens_out,
            tokens_known,
            cached_tokens_in,
            cached_tokens_known,
        )
        self.query_one("#project-metrics", Static).update(display)

    def update_flight_plan(
        self,
        flight_plan: FlightPlan,
        execution_state: Any | None = None,
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
            self._execution_state = _state_name(execution_state)
        tree = self.query_one("#waypoint-tree", FlightPlanTree)
        tree.update_flight_plan(flight_plan, cost_by_waypoint)
        self._update_overall_progress()

    def update_execution_state(self, state: Any) -> None:
        """Update the execution state indicator."""
        self._execution_state = _state_name(state)
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
            "idle": ("", ""),
            "running": (" ▶ Running", "bold cyan"),
            "pause_pending": (" ⏸ Pausing...", "bold yellow"),
            "paused": (" ⏸ Paused", "bold yellow"),
            "done": (" ✓ Done", "bold green"),
            "intervention": (" ⚠ Needs Help", "bold red"),
        }
        state_text, state_style = state_styles.get(self._execution_state, ("", ""))
        if state_text:
            # Blink the icon when running or pause pending
            if (
                self._execution_state in ("running", "pause_pending")
                and not self._blink_visible
            ):
                # Show text without the icon symbol
                if self._execution_state == "running":
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
