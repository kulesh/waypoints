"""FLY screen for waypoint implementation."""

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Footer, Static, Tree
from textual.worker import Worker, WorkerFailed

if TYPE_CHECKING:
    from waypoints.tui.app import WaypointsApp

from waypoints.fly.evidence import FileOperation
from waypoints.fly.execution_log import (
    ExecutionLogReader,
)
from waypoints.fly.executor import (
    ExecutionContext,
    ExecutionResult,
)
from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionNeededError,
    InterventionResult,
)
from waypoints.git import GitConfig, ReceiptValidator
from waypoints.models import JourneyState, Project
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration import JourneyCoordinator
from waypoints.orchestration.coordinator_fly import prepare_waypoint_for_rerun
from waypoints.orchestration.fly_presenter import build_status_line
from waypoints.orchestration.fly_service import FlyService
from waypoints.orchestration.types import NextAction
from waypoints.runtime import TimeoutDomain, get_command_runner
from waypoints.tui.screens.fly_controller import FlyController
from waypoints.tui.screens.fly_session import FlySession
from waypoints.tui.screens.fly_status import derive_state_message, format_countdown
from waypoints.tui.screens.fly_timers import (
    activate_budget_wait,
    clear_budget_wait,
    elapsed_seconds,
    evaluate_budget_wait_tick,
    transition_execution_timers,
)
from waypoints.tui.screens.intervention import InterventionModal
from waypoints.tui.widgets.file_preview import FilePreviewModal
from waypoints.tui.widgets.flight_plan import DebugWaypointModal
from waypoints.tui.widgets.fly_detail_panel import (
    ExecutionLogViewMode,
    WaypointDetailPanel,
)
from waypoints.tui.widgets.fly_execution_log import ExecutionLog
from waypoints.tui.widgets.fly_waypoint_list_panel import (
    WaypointListPanel,
    format_project_metrics,
)
from waypoints.tui.widgets.header import StatusHeader
from waypoints.tui.widgets.resizable_split import ResizableSplit

logger = logging.getLogger(__name__)
_format_project_metrics = format_project_metrics


class ExecutionState(Enum):
    """State of waypoint execution."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSE_PENDING = "pause_pending"  # Pause requested, finishing current waypoint
    PAUSED = "paused"
    DONE = "done"
    INTERVENTION = "intervention"


def get_git_status_summary(project_path: Path) -> str:
    """Get git status with colored indicator: 'branch [color]●[/] N changed'."""
    try:
        runner = get_command_runner()
        # Get current branch
        branch_result = runner.run(
            command=["git", "branch", "--show-current"],
            domain=TimeoutDomain.UI_GIT_PROBE,
            cwd=project_path,
        )
        if branch_result.effective_exit_code != 0:
            return ""  # Not a git repo
        branch = branch_result.stdout.strip() or "HEAD"

        # Get status (use -uall to show individual files in untracked directories)
        status_result = runner.run(
            command=["git", "status", "--porcelain", "-uall"],
            domain=TimeoutDomain.UI_GIT_PROBE,
            cwd=project_path,
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


class FlyScreen(Screen[None]):
    """FLY phase - waypoint implementation screen."""

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("r", "start", "Run", show=True),
        Binding("p", "pause", "Pause", show=True),
        Binding("s", "skip", "Skip", show=True),
        Binding("d", "debug_waypoint", "Debug", show=True),
        Binding("x", "toggle_log_view", "Raw/Sum", show=True),
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
        self._controller = FlyController(self.coordinator)
        self._fly_service = FlyService(self.coordinator)
        self._session = FlySession()

        # Track live criteria completion for cross-check with receipt
        self._live_criteria_completed: set[int] = set()

    def _ensure_session(self) -> FlySession:
        session = getattr(self, "_session", None)
        if session is None:
            session = FlySession()
            self._session = session
        return session

    # Compatibility accessors during FlySession migration.
    @property
    def _additional_iterations(self) -> int:
        return self._ensure_session().additional_iterations

    @_additional_iterations.setter
    def _additional_iterations(self, value: int) -> None:
        self._ensure_session().additional_iterations = value

    @property
    def _execution_start(self) -> datetime | None:
        return self._ensure_session().execution_start

    @_execution_start.setter
    def _execution_start(self, value: datetime | None) -> None:
        self._ensure_session().execution_start = value

    @property
    def _elapsed_before_pause(self) -> float:
        return self._ensure_session().elapsed_before_pause

    @_elapsed_before_pause.setter
    def _elapsed_before_pause(self, value: float) -> None:
        self._ensure_session().elapsed_before_pause = value

    @property
    def _ticker_timer(self) -> Timer | None:
        return self._ensure_session().ticker_timer

    @_ticker_timer.setter
    def _ticker_timer(self, value: Timer | None) -> None:
        self._ensure_session().ticker_timer = value

    @property
    def _budget_wait_timer(self) -> Timer | None:
        return self._ensure_session().budget_wait_timer

    @_budget_wait_timer.setter
    def _budget_wait_timer(self, value: Timer | None) -> None:
        self._ensure_session().budget_wait_timer = value

    @property
    def _budget_resume_at(self) -> datetime | None:
        return self._ensure_session().budget_resume_at

    @_budget_resume_at.setter
    def _budget_resume_at(self, value: datetime | None) -> None:
        self._ensure_session().budget_resume_at = value

    @property
    def _budget_resume_waypoint_id(self) -> str | None:
        return self._ensure_session().budget_resume_waypoint_id

    @_budget_resume_waypoint_id.setter
    def _budget_resume_waypoint_id(self, value: str | None) -> None:
        self._ensure_session().budget_resume_waypoint_id = value

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
        # Load persisted host validation preference for this project
        self.waypoints_app.host_validations_enabled = (
            self.waypoints_app.load_host_validation_preference(self.project)
        )
        # Reflect initial state in status bar
        self._update_status_bar(self.execution_state)

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

    def on_unmount(self) -> None:
        """Stop active timers when leaving the screen."""
        transition_execution_timers(
            self._ensure_session(),
            state=ExecutionState.IDLE.value,
            start_ticker=lambda: self.set_interval(1.0, self._update_ticker),
        )
        clear_budget_wait(self._ensure_session())
        git_timer = getattr(self, "_git_status_timer", None)
        if git_timer:
            git_timer.stop()

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
        cached_tokens_in: int | None = None
        cached_tokens_known = False
        if self.waypoints_app.metrics_collector:
            metrics = self.waypoints_app.metrics_collector
            cost = metrics.total_cost
            tokens_in = metrics.total_tokens_in
            tokens_out = metrics.total_tokens_out
            cached_tokens_in = metrics.total_cached_tokens_in
            tokens_known = metrics.has_token_usage_data()
            cached_tokens_known = metrics.has_cached_token_usage_data()
        time_seconds = self._calculate_total_execution_time()
        list_panel = self.query_one(WaypointListPanel)
        list_panel.update_project_metrics(
            cost,
            time_seconds,
            tokens_in,
            tokens_out,
            tokens_known,
            cached_tokens_in,
            cached_tokens_known,
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
            cached_tokens_in = self._get_waypoint_cached_tokens_in(waypoint.id)
            detail_panel.show_waypoint(
                waypoint,
                project=self.project,
                active_waypoint_id=active_id,
                cost=cost,
                tokens=tokens,
                cached_tokens_in=cached_tokens_in,
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

    def _get_waypoint_cached_tokens_in(self, waypoint_id: str) -> int | None:
        """Get cached input tokens for a waypoint from the metrics collector."""
        if self.waypoints_app.metrics_collector:
            cached_by_waypoint = (
                self.waypoints_app.metrics_collector.cached_tokens_by_waypoint()
            )
            return cached_by_waypoint.get(waypoint_id)
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

        Delegates selection/completion classification to FlyService, then updates UI.

        Args:
            include_in_progress: If True, also consider IN_PROGRESS and FAILED
                                waypoints (for resume after pause/failure)
        """
        logger.debug(
            "=== Selection round (include_in_progress=%s) ===", include_in_progress
        )

        next_action = self._fly_service.select_next_waypoint_action(
            include_failed=include_in_progress
        )
        wp = next_action.waypoint

        if wp:
            # Waypoint selected - update UI
            logger.info("SELECTED %s via coordinator", wp.id)
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            cost = self._get_waypoint_cost(wp.id)
            tokens = self._get_waypoint_tokens(wp.id)
            cached_tokens_in = self._get_waypoint_cached_tokens_in(wp.id)
            detail_panel.show_waypoint(
                wp,
                project=self.project,
                active_waypoint_id=None,
                cost=cost,
                tokens=tokens,
                cached_tokens_in=cached_tokens_in,
            )
            return

        # No eligible waypoint selected; follow service action.
        if next_action.action == "complete":
            logger.info("All waypoints complete - DONE")
            self.execution_state = ExecutionState.DONE
        else:
            logger.info(next_action.message or "No executable waypoint available")
            self.execution_state = ExecutionState.PAUSED

    def _get_state_message(self, state: ExecutionState) -> str:
        """Get the status bar message for a given execution state."""
        return derive_state_message(
            state=state.value,
            current_waypoint=self.current_waypoint,
            get_completion_status=self._get_completion_status,
            budget_resume_waypoint_id=self._ensure_session().budget_resume_waypoint_id,
            budget_resume_at=self._ensure_session().budget_resume_at,
        )

    def _format_countdown(self, total_seconds: int) -> str:
        """Format a countdown for status-bar display."""
        return format_countdown(total_seconds)

    def _clear_budget_wait(self) -> None:
        """Clear any active budget-wait countdown state."""
        clear_budget_wait(self._ensure_session())

    def _activate_budget_wait(
        self, intervention: Intervention, log: ExecutionLog
    ) -> None:
        """Pause execution with budget-specific messaging and optional countdown."""
        # Delegate business logic: extract budget details and mark pending
        details = self.coordinator.compute_budget_wait(
            intervention,
            current_waypoint_id=(
                self.current_waypoint.id if self.current_waypoint else None
            ),
        )
        # Budget pause is recoverable work: keep waypoint pending, not failed.
        if self.current_waypoint:
            self.coordinator.mark_waypoint_status(
                self.current_waypoint,
                WaypointStatus.PENDING,
            )

        # Log budget usage
        if (
            details.configured_budget_usd is not None
            and details.current_cost_usd is not None
        ):
            log.write_log(
                f"Budget usage: ${details.current_cost_usd:.2f} / "
                f"${details.configured_budget_usd:.2f}"
            )
        elif details.current_cost_usd is not None:
            log.write_log(f"Budget usage so far: ${details.current_cost_usd:.2f}")
        log.write_log("Progress saved. Workspace state is preserved for resume.")

        # Transition to paused and surface clear status.
        self.coordinator.transition(JourneyState.FLY_PAUSED)
        self.execution_state = ExecutionState.PAUSED
        self.query_one(StatusHeader).set_normal()
        self._refresh_waypoint_list()

        remaining_secs = activate_budget_wait(
            self._ensure_session(),
            waypoint_id=details.waypoint_id,
            resume_at=details.resume_at,
            start_timer=lambda: self.set_interval(1.0, self._on_budget_wait_tick),
        )
        if remaining_secs is not None:
            self.notify(
                "Budget limit reached. "
                f"Auto-resume in {self._format_countdown(remaining_secs)}.",
                severity="warning",
            )
        else:
            self.notify(
                "Budget limit reached. Execution paused; resume after budget reset.",
                severity="warning",
            )

    def _on_budget_wait_tick(self) -> None:
        """Update budget countdown and auto-resume when reset time is reached."""
        decision = evaluate_budget_wait_tick(self._ensure_session())
        if decision.should_refresh_status:
            self._update_status_bar(self.execution_state)
            return

        target_waypoint_id = decision.resume_waypoint_id

        if self.execution_state != ExecutionState.PAUSED:
            return

        if target_waypoint_id and self.flight_plan:
            resumed = self.flight_plan.get_waypoint(target_waypoint_id)
            if resumed is not None:
                self.current_waypoint = resumed

        if (
            self.current_waypoint
            and self.current_waypoint.status == WaypointStatus.FAILED
        ):
            self.coordinator.mark_waypoint_status(
                self.current_waypoint, WaypointStatus.PENDING
            )

        if not self.current_waypoint:
            self._select_next_waypoint(include_in_progress=True)
            if not self.current_waypoint:
                self.notify(
                    "Budget reset reached, but no waypoint is ready to resume.",
                    severity="warning",
                )
                return

        self.notify("Budget reset window reached. Resuming execution.")
        self.coordinator.transition(JourneyState.FLY_EXECUTING)
        self.execution_state = ExecutionState.RUNNING
        self.query_one(StatusHeader).set_normal()
        self._execute_current_waypoint()

    def _update_ticker(self) -> None:
        """Update the status bar with elapsed time and cost."""
        total_elapsed = elapsed_seconds(self._ensure_session())
        if total_elapsed is None:
            return

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
            build_status_line(
                host_label=host_label,
                message=message,
                cost=cost,
                elapsed_seconds=(minutes * 60) + seconds,
            )
        )

    def _update_status_bar(self, state: ExecutionState) -> None:
        """Update the status bar with state message and optional cost."""
        status_bar = self.query_one("#status-bar", Static)
        message = self._get_state_message(state)
        host_label = self._host_validation_label()

        # Update action hint in left panel
        list_panel = self.query_one(WaypointListPanel)
        list_panel.update_action_hint(message)

        if state == ExecutionState.RUNNING and self._ensure_session().execution_start:
            # Timer callback will handle updates
            return

        # Show cost even when not running (if there's any)
        cost = (
            self.waypoints_app.metrics_collector.total_cost
            if self.waypoints_app.metrics_collector
            else 0.0
        )
        status_bar.update(
            build_status_line(
                host_label=host_label,
                message=message,
                cost=cost,
            )
        )

    def _host_validation_label(self) -> str:
        """Return a short label for host validation mode."""
        if self.waypoints_app.host_validations_enabled:
            return "HostVal: ON"
        return "HostVal: OFF (LLM-as-judge)"

    def watch_execution_state(self, state: ExecutionState) -> None:
        """Update UI when execution state changes."""
        transition_execution_timers(
            self._ensure_session(),
            state=state.value,
            start_ticker=lambda: self.set_interval(1.0, self._update_ticker),
        )

        # Update status bar
        self._update_status_bar(state)

        # Update progress bar with execution state
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        list_panel.update_execution_state(state)

    def action_start(self) -> None:
        """Start or resume waypoint execution."""
        self._clear_budget_wait()

        # Check if user has selected a specific failed waypoint to retry
        list_panel = self.query_one("#waypoint-list", WaypointListPanel)
        selected = list_panel.selected_waypoint
        start_decision = self._controller.start(selected, self.current_waypoint)

        if (
            start_decision.action == "rerun_selected"
            and start_decision.waypoint is not None
        ):
            selected = start_decision.waypoint
            # User wants to rerun this specific waypoint.
            prior_status = selected.status
            prepare_waypoint_for_rerun(selected)
            self.coordinator.mark_waypoint_status(selected, WaypointStatus.PENDING)
            self._refresh_waypoint_list()
            self.current_waypoint = selected
            action_label = (
                "Retrying" if prior_status == WaypointStatus.FAILED else "Re-running"
            )
            self.notify(f"{action_label} {selected.id}")

            # Update detail panel to show this waypoint
            detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
            cost = self._get_waypoint_cost(selected.id)
            tokens = self._get_waypoint_tokens(selected.id)
            cached_tokens_in = self._get_waypoint_cached_tokens_in(selected.id)
            detail_panel.show_waypoint(
                selected,
                project=self.project,
                active_waypoint_id=None,
                cost=cost,
                tokens=tokens,
                cached_tokens_in=cached_tokens_in,
            )

            # Transition journey state and execute
            journey = self.project.journey
            if journey and journey.state in (
                JourneyState.FLY_PAUSED,
                JourneyState.FLY_INTERVENTION,
            ):
                self.coordinator.transition(JourneyState.FLY_EXECUTING)
            elif journey and journey.state in (
                JourneyState.CHART_REVIEW,
                JourneyState.LAND_REVIEW,
            ):
                self.coordinator.transition(JourneyState.FLY_READY)
                self.coordinator.transition(JourneyState.FLY_EXECUTING)
            else:
                self.coordinator.transition(JourneyState.FLY_EXECUTING)
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
            self.coordinator.transition(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            self._execute_current_waypoint()
            return

        if not self.current_waypoint:
            self._select_next_waypoint()
            if not self.current_waypoint:
                self.notify("No waypoints ready to execute")
                return

        # Transition journey state to FLY_EXECUTING
        # Handle case where we came from Chart or Land (CHART_REVIEW/LAND_REVIEW)
        journey = self.project.journey
        if journey and journey.state in (
            JourneyState.CHART_REVIEW,
            JourneyState.LAND_REVIEW,
        ):
            self.coordinator.transition(JourneyState.FLY_READY)
        self.coordinator.transition(JourneyState.FLY_EXECUTING)
        self.execution_state = ExecutionState.RUNNING
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
        pause_decision = self._controller.pause(
            is_running=self.execution_state == ExecutionState.RUNNING
        )
        if pause_decision.action == "pause_pending":
            self.execution_state = ExecutionState.PAUSE_PENDING
            self.coordinator.cancel_execution()
            self.coordinator.log_pause()
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
            cached_tokens_in = self._get_waypoint_cached_tokens_in(debug_wp.id)
            detail_panel.show_waypoint(
                debug_wp,
                project=self.project,
                active_waypoint_id=None,
                cost=cost,
                tokens=tokens,
                cached_tokens_in=cached_tokens_in,
            )
            self.notify(f"Debug waypoint created: {debug_wp.id}")

        self.app.push_screen(DebugWaypointModal(), handle_result)

    def action_toggle_log_view(self) -> None:
        """Toggle waypoint history log between raw and summary views."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        mode = detail_panel.toggle_log_view_mode()
        active_waypoint_id = (
            self.current_waypoint.id
            if self.current_waypoint and self.execution_state == ExecutionState.RUNNING
            else None
        )
        detail_panel.refresh_current_waypoint(active_waypoint_id=active_waypoint_id)
        mode_label = "Raw" if mode == ExecutionLogViewMode.RAW else "Summary"
        self.notify(f"Log view: {mode_label}")

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
        spec = self.waypoints_app.load_latest_doc(self.project, "product-spec")
        brief = self.waypoints_app.load_latest_doc(self.project, "idea-brief")
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

        self._clear_budget_wait()

        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        # Update status to IN_PROGRESS
        self.coordinator.mark_waypoint_status(
            self.current_waypoint, WaypointStatus.IN_PROGRESS
        )

        # Mark this as the active waypoint for output tracking
        detail_panel.set_live_output_waypoint(self.current_waypoint.id)

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

        max_iters = MAX_ITERATIONS + self._additional_iterations
        self._additional_iterations = 0  # Reset for next execution

        # Create executor via coordinator (stores it for cancel/logging)
        self.coordinator.create_executor(
            waypoint=self.current_waypoint,
            spec=self.spec,
            on_progress=self._on_execution_progress,
            max_iterations=max_iters,
            host_validations_enabled=self.waypoints_app.host_validations_enabled,
        )
        # Run execution in background worker
        self.run_worker(
            self._run_executor(),
            name="waypoint_executor",
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )

    async def _run_executor(self) -> ExecutionResult:
        """Run the executor asynchronously."""
        executor = self.coordinator.active_executor
        if not executor:
            return ExecutionResult.FAILED
        try:
            return await executor.execute()
        except InterventionNeededError as err:
            self.coordinator.store_worker_intervention(err.intervention)
            return ExecutionResult.INTERVENTION_NEEDED

    def _on_execution_progress(self, ctx: ExecutionContext) -> None:
        """Handle progress updates from the executor.

        Uses call_later to safely schedule UI updates from any thread context.
        """
        self.app.call_later(self._update_progress_ui, ctx)

    def _update_progress_ui(self, ctx: ExecutionContext) -> None:
        """Update UI with progress (called on main thread)."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)

        # Guard: Only update if this waypoint's output is currently displayed
        if not detail_panel.is_showing_output_for(ctx.waypoint.id):
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
            elif ctx.output.strip():
                log.write_log(f"[dim]→ {ctx.output.strip()}[/]")
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
            worker_error: BaseException | None = None
            worker_result: ExecutionResult | None = None
            if event.worker.state.name == "ERROR":
                try:
                    _ = event.worker.result
                except WorkerFailed as wf:
                    worker_error = cast(
                        BaseException,
                        getattr(wf, "error", None) or wf.__cause__ or wf,
                    )
                except Exception as exc:  # noqa: BLE001
                    worker_error = exc
            else:
                worker_result = event.worker.result

            pending_intervention = (
                self.coordinator.take_worker_intervention()
                if worker_result == ExecutionResult.INTERVENTION_NEEDED
                else None
            )
            decision = self._controller.handle_worker_result(
                worker_error=worker_error,
                worker_result=worker_result,
                pending_worker_intervention=pending_intervention,
            )
            if decision.action == "handle_intervention" and decision.intervention:
                self._handle_intervention(decision.intervention)
                return

            if decision.action == "handle_failure" and worker_error is not None:
                logger.exception("Worker failed with exception: %s", worker_error)
            self._handle_execution_result(decision.result)

    def _handle_execution_result(self, result: ExecutionResult | None) -> None:
        """Handle the result of waypoint execution.

        Delegates ALL status mutations to the coordinator, then dispatches
        on the returned NextAction to apply UI effects (logging, notifications,
        execution state transitions, screen navigation).
        """
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        # Update header cost display after execution
        self.waypoints_app.update_header_cost()
        self._update_project_metrics()

        if self.current_waypoint:
            cost = self._get_waypoint_cost(self.current_waypoint.id)
            tokens = self._get_waypoint_tokens(self.current_waypoint.id)
            cached_tokens_in = self._get_waypoint_cached_tokens_in(
                self.current_waypoint.id
            )
            detail_panel.update_metrics(cost, tokens, cached_tokens_in)

        config = GitConfig.load(self.project.slug)
        decision = self._controller.handle_execution_result(
            current_waypoint=self.current_waypoint,
            result=result,
            git_config=config,
        )
        if decision.action == "missing_waypoint":
            log.log_error("Execution failed")
            self.coordinator.transition(JourneyState.FLY_INTERVENTION)
            self.execution_state = ExecutionState.INTERVENTION
            self.query_one(StatusHeader).set_error()
            self.notify("Waypoint execution failed", severity="error")
            return

        next_action = decision.next_action
        completed_waypoint = decision.completed_waypoint
        if next_action is None or completed_waypoint is None:
            log.log_error("Execution failed")
            self.execution_state = ExecutionState.INTERVENTION
            return

        # Dispatch on coordinator's decision
        if next_action.action in ("continue", "complete"):
            self._apply_success_effects(
                log, detail_panel, next_action, completed_waypoint
            )
        elif next_action.action == "intervention":
            log.log_error(next_action.message or "Execution failed")
            self.coordinator.transition(JourneyState.FLY_INTERVENTION)
            self.execution_state = ExecutionState.INTERVENTION
            self.query_one(StatusHeader).set_error()
            self._refresh_waypoint_list()
            self.notify(
                next_action.message or "Waypoint execution failed",
                severity="error",
            )
        elif next_action.action == "pause":
            log.write_log(next_action.message or "Execution paused")
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED

    def _apply_success_effects(
        self,
        log: ExecutionLog,
        detail_panel: WaypointDetailPanel,
        next_action: NextAction,
        completed_waypoint: Waypoint,
    ) -> None:
        """Apply UI effects for a successful waypoint completion."""
        # Log success and verification summary
        log.log_success(f"Waypoint {completed_waypoint.id} complete!")

        self._live_criteria_completed = ExecutionLogReader.get_completed_criteria(
            self.project, completed_waypoint.id
        )
        self._log_verification_summary(completed_waypoint, log)

        # Log commit result
        cr = next_action.commit_result
        if cr and cr.committed:
            self.notify(f"Committed: {completed_waypoint.id}")
            self.coordinator.log_git_commit(True, cr.commit_hash or "", cr.message)
        elif cr and not cr.committed and cr.message:
            if "Nothing to commit" not in cr.message:
                self.notify(f"Skipping commit: {cr.message}", severity="warning")
            self.coordinator.log_git_commit(False, "", cr.message)
        if cr and cr.initialized_repo:
            self.notify("Initialized git repository")

        # Reset live criteria tracking for next waypoint
        self._live_criteria_completed = set()

        detail_panel.clear_iteration()
        self._refresh_waypoint_list()

        # Move to next waypoint if not paused/pausing
        if self.execution_state == ExecutionState.RUNNING:
            if next_action.action == "continue" and next_action.waypoint:
                self.current_waypoint = next_action.waypoint
                self._execute_current_waypoint()
            elif next_action.action == "complete":
                self._select_next_waypoint()  # sets DONE state
                if self.execution_state == ExecutionState.DONE:  # type: ignore[comparison-overlap]
                    self.coordinator.transition(JourneyState.LAND_REVIEW)
                    self._switch_to_land_screen()
        elif self.execution_state == ExecutionState.PAUSE_PENDING:
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED

    def _handle_intervention(self, intervention: Intervention) -> None:
        """Handle an intervention request by classifying and presenting it."""
        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        # Log the intervention
        type_label = intervention.type.value.replace("_", " ").title()
        log.log_error(f"Intervention needed: {type_label}")
        log.write_log(intervention.error_summary[:500])

        decision = self._controller.handle_intervention_display(
            intervention,
            self.current_waypoint,
        )
        if not decision.show_modal:
            # Budget-exceeded: auto-handle without user interaction
            self._activate_budget_wait(intervention, log)
            self.coordinator.clear_intervention()
            return

        if decision.should_mark_failed and self.current_waypoint:
            self.coordinator.mark_waypoint_status(
                self.current_waypoint, WaypointStatus.FAILED
            )
            self._refresh_waypoint_list()

        # Transition journey state: FLY_EXECUTING -> FLY_INTERVENTION
        self.coordinator.transition(JourneyState.FLY_INTERVENTION)
        self.execution_state = ExecutionState.INTERVENTION
        self.query_one(StatusHeader).set_error()

        # Show the intervention modal
        self.app.push_screen(
            InterventionModal(intervention),
            callback=self._on_intervention_result,
        )

    def _on_intervention_result(self, result: InterventionResult | None) -> None:
        """Handle the result of the intervention modal."""
        current = self.coordinator.current_intervention
        resolution = self._controller.handle_intervention_result(
            result=result,
            current_intervention=current,
        )

        if resolution.action == "cancelled":
            # User cancelled - treat as abort
            self.notify("Intervention cancelled")
            self.coordinator.log_intervention_resolved("cancelled")
            self.coordinator.clear_intervention()
            return

        detail_panel = self.query_one("#waypoint-detail", WaypointDetailPanel)
        log = detail_panel.execution_log

        # Log the intervention resolution to execution log
        params: dict[str, Any] = {}
        if result and result.action == InterventionAction.RETRY:
            params["additional_iterations"] = result.additional_iterations
        elif result and result.action == InterventionAction.ROLLBACK:
            params["rollback_tag"] = result.rollback_tag
        if result is not None:
            self.coordinator.log_intervention_resolved(result.action.value, **params)
        if resolution.action == "missing_context":
            log.log_error("Missing intervention context; pausing execution")
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self.query_one(StatusHeader).set_normal()
            self.coordinator.clear_intervention()
            return

        # Keep UI-level rollback side effect (git reset + plan reload) while
        # delegating status mutation and next-action selection to coordinator.
        if result and result.action == InterventionAction.ROLLBACK:
            log.write_log("Rolling back to last safe tag")
            self._rollback_to_safe_tag(result.rollback_tag)

        if resolution.action == "budget_wait" and current is not None:
            self._activate_budget_wait(current, log)
            self.coordinator.clear_intervention()
            return

        next_action = resolution.next_action
        if next_action is None:
            log.write_log("Execution paused")
            self.execution_state = ExecutionState.PAUSED
            self.coordinator.clear_intervention()
            return

        self._refresh_waypoint_list()

        if next_action.action == "continue":
            if resolution.retry_iterations > 0:
                self._additional_iterations = resolution.retry_iterations
            self.current_waypoint = next_action.waypoint
            log.write_log(next_action.message or "Continuing execution")
            self.coordinator.transition(JourneyState.FLY_EXECUTING)
            self.execution_state = ExecutionState.RUNNING
            self.query_one(StatusHeader).set_normal()
            if self.current_waypoint is not None:
                self._execute_current_waypoint()
            else:
                self.execution_state = ExecutionState.PAUSED
                self.notify("No waypoint available to continue", severity="warning")

        elif next_action.action == "complete":
            log.write_log(next_action.message or "All waypoints complete")
            self.execution_state = ExecutionState.DONE
            self.coordinator.transition(JourneyState.LAND_REVIEW)
            self.notify("All waypoints complete!")
            self._switch_to_land_screen()

        elif next_action.action == "abort":
            log.write_log(next_action.message or "Execution aborted")
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self.query_one(StatusHeader).set_normal()
            self.notify("Execution aborted")

        else:
            log.write_log(next_action.message or "Execution paused")
            self.coordinator.transition(JourneyState.FLY_PAUSED)
            self.execution_state = ExecutionState.PAUSED
            self.query_one(StatusHeader).set_normal()
            if result and result.action == InterventionAction.EDIT:
                self.notify(
                    "Edit waypoint in flight plan, then press 'r' to retry",
                    severity="information",
                )
            elif result and result.action == InterventionAction.WAIT:
                self.notify("Execution paused")

        self.coordinator.clear_intervention()

    def _rollback_to_safe_tag(self, tag: str | None) -> None:
        """Rollback git to the specified tag or find the last safe one."""
        result = self.coordinator.rollback_to_tag(tag)
        if result.success:
            self.notify(result.message)
            if result.flight_plan:
                self.flight_plan = result.flight_plan
                self._refresh_waypoint_list()
        elif "No rollback tag" in result.message:
            self.notify(result.message, severity="warning")
        else:
            self.notify(result.message, severity="error")

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
                detail_panel.log_soft_validation_evidence(
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
