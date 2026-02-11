"""FLY phase delegate — waypoint selection, execution, result handling, git.

Owns the business logic for executing waypoints: selecting the next
waypoint, driving the executor, handling results, checking parent
completion, managing interventions, and committing to git.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from waypoints.fly.executor import ExecutionContext, ExecutionResult, WaypointExecutor
from waypoints.fly.intervention import (
    Intervention,
    InterventionAction,
    InterventionType,
)
from waypoints.models import Waypoint, WaypointStatus
from waypoints.orchestration.coordinator_fly import (
    build_completion_status,
    build_intervention_resolution,
    build_next_action_after_success,
    select_next_waypoint_candidate,
)
from waypoints.orchestration.fly_git import (
    commit_waypoint as commit_waypoint_with_policy,
)
from waypoints.orchestration.fly_git import (
    rollback_to_tag as rollback_to_tag_with_policy,
)
from waypoints.orchestration.types import (
    BudgetWaitDetails,
    CommitResult,
    CompletionStatus,
    InterventionPresentation,
    NextAction,
    ProgressCallback,
    ProgressUpdate,
    RollbackResult,
)

if TYPE_CHECKING:
    from waypoints.git.config import GitConfig
    from waypoints.orchestration.coordinator import JourneyCoordinator

logger = logging.getLogger(__name__)


class FlyPhase:
    """Waypoint selection, execution, result handling, and git integration."""

    def __init__(self, coordinator: "JourneyCoordinator") -> None:
        self._coord = coordinator
        self._active_executor: WaypointExecutor | None = None
        self._current_intervention: Intervention | None = None
        self._pending_worker_intervention: Intervention | None = None

    # ─── Waypoint Selection ───────────────────────────────────────────

    def select_next_waypoint(
        self,
        include_failed: bool = False,
    ) -> Waypoint | None:
        """Find the next waypoint to execute.

        Selection logic:
        1. If include_failed, check for IN_PROGRESS or FAILED waypoints first
        2. Find first PENDING waypoint with all dependencies met
        3. Epics are eligible once all their children complete

        Args:
            include_failed: Whether to consider failed/in-progress waypoints

        Returns:
            Next waypoint to execute, or None if all complete/blocked
        """
        waypoint = select_next_waypoint_candidate(
            self._coord.flight_plan,
            include_failed=include_failed,
        )
        self._coord.current_waypoint = waypoint

        if waypoint is not None:
            if include_failed and waypoint.status in (
                WaypointStatus.IN_PROGRESS,
                WaypointStatus.FAILED,
            ):
                logger.info(
                    "Resuming %s waypoint: %s",
                    waypoint.status.value,
                    waypoint.id,
                )
            else:
                logger.info("Selected next waypoint: %s", waypoint.id)
        else:
            logger.info("No waypoints available to execute")

        return waypoint

    # ─── Execution ────────────────────────────────────────────────────

    def create_executor(
        self,
        waypoint: Waypoint,
        spec: str,
        on_progress: Callable[[ExecutionContext], None] | None = None,
        max_iterations: int = 10,
        host_validations_enabled: bool = True,
    ) -> WaypointExecutor:
        """Create and store the active executor for a waypoint.

        FlyScreen calls this instead of constructing WaypointExecutor directly.
        The executor is stored so cancel_execution() can reach it.

        Args:
            waypoint: The waypoint to execute
            spec: Product specification text
            on_progress: Raw ExecutionContext callback (UI layer provides this)
            max_iterations: Maximum execution iterations
            host_validations_enabled: Whether to run host validations

        Returns:
            The WaypointExecutor instance (caller runs it via worker)
        """
        self._active_executor = WaypointExecutor(
            project=self._coord.project,
            waypoint=waypoint,
            spec=spec,
            on_progress=on_progress,
            max_iterations=max_iterations,
            metrics_collector=self._coord.metrics,
            host_validations_enabled=host_validations_enabled,
        )
        return self._active_executor

    def cancel_execution(self) -> None:
        """Cancel the active executor, if any."""
        if self._active_executor is not None:
            self._active_executor.cancel()

    def clear_executor(self) -> None:
        """Clear the stored executor reference after execution completes."""
        self._active_executor = None

    @property
    def active_executor(self) -> WaypointExecutor | None:
        """The currently active executor, or None."""
        return self._active_executor

    # ─── Execution Logging ────────────────────────────────────────────

    def log_pause(self) -> None:
        """Log a pause event to the active executor's log."""
        ex = self._active_executor
        if ex is not None:
            ex.log_pause_event()

    def log_git_commit(self, success: bool, commit_hash: str, message: str) -> None:
        """Log a git commit event to the active executor's log."""
        ex = self._active_executor
        if ex is not None:
            ex.log_git_commit_event(success, commit_hash, message)

    def log_intervention_resolved(self, action: str, **params: Any) -> None:
        """Log intervention resolution to the active executor's log."""
        ex = self._active_executor
        if ex is not None:
            ex.log_intervention_resolved_event(action, **params)

    # ─── Legacy execute_waypoint (used by headless callers) ──────────

    async def execute_waypoint(
        self,
        waypoint: Waypoint,
        on_progress: ProgressCallback | None = None,
        max_iterations: int = 10,
        host_validations_enabled: bool = True,
    ) -> ExecutionResult:
        """Execute a waypoint using the AI executor.

        This is the self-contained path for headless/CI callers.
        TUI callers use create_executor() + their own worker pattern.

        Args:
            waypoint: The waypoint to execute
            on_progress: Callback for progress updates
            max_iterations: Maximum execution iterations
            host_validations_enabled: Whether to run host validations

        Returns:
            ExecutionResult indicating success/failure

        Raises:
            InterventionNeededError: When human intervention is required
        """
        # Mark as in progress
        waypoint.status = WaypointStatus.IN_PROGRESS
        self._coord.save_flight_plan()

        spec = self._coord.product_spec
        executor = self.create_executor(
            waypoint=waypoint,
            spec=spec,
            on_progress=self._wrap_progress_callback(on_progress),
            max_iterations=max_iterations,
            host_validations_enabled=host_validations_enabled,
        )

        try:
            return await executor.execute()
        finally:
            self.clear_executor()

    def _wrap_progress_callback(
        self,
        callback: ProgressCallback | None,
    ) -> Callable[[ExecutionContext], None] | None:
        """Wrap user callback to convert ExecutionContext to ProgressUpdate."""
        if callback is None:
            return None

        def wrapper(ctx: ExecutionContext) -> None:
            update = ProgressUpdate(
                waypoint_id=ctx.waypoint.id,
                iteration=ctx.iteration,
                total_iterations=ctx.total_iterations,
                step=ctx.step,
                output=ctx.output,
                criteria_completed=ctx.criteria_completed,
            )
            callback(update)

        return wrapper

    # ─── Intervention Classification ─────────────────────────────────

    def classify_intervention(
        self, intervention: Intervention
    ) -> InterventionPresentation:
        """Decide how the UI should present an intervention.

        Budget-exceeded interventions are auto-handled (budget wait).
        Everything else requires a modal.
        """
        self._current_intervention = intervention
        show_modal = intervention.type != InterventionType.BUDGET_EXCEEDED
        return InterventionPresentation(
            show_modal=show_modal,
            intervention=intervention,
        )

    def store_worker_intervention(self, intervention: Intervention) -> None:
        """Store an intervention caught by the worker thread for main-thread pickup."""
        self._pending_worker_intervention = intervention

    def take_worker_intervention(self) -> Intervention | None:
        """Consume and return the pending worker intervention, if any."""
        intervention = self._pending_worker_intervention
        self._pending_worker_intervention = None
        return intervention

    def clear_intervention(self) -> None:
        """Clear the current intervention state."""
        self._current_intervention = None

    @property
    def current_intervention(self) -> Intervention | None:
        """The active intervention, or None."""
        return self._current_intervention

    def compute_budget_wait(
        self,
        intervention: Intervention,
        current_waypoint_id: str | None = None,
    ) -> BudgetWaitDetails:
        """Extract budget wait details from an intervention.

        Parses the intervention context to determine resume timestamp,
        configured budget, and current cost. UI uses these to display
        a countdown and auto-resume.
        """
        waypoint_id = current_waypoint_id or intervention.waypoint.id

        resume_at: datetime | None = None
        resume_at_raw = intervention.context.get("resume_at_utc")
        if isinstance(resume_at_raw, str):
            try:
                resume_at = datetime.fromisoformat(resume_at_raw)
                if resume_at.tzinfo is None:
                    resume_at = resume_at.replace(tzinfo=UTC)
            except ValueError:
                pass

        configured = intervention.context.get("configured_budget_usd")
        current = intervention.context.get("current_cost_usd")

        return BudgetWaitDetails(
            waypoint_id=waypoint_id,
            resume_at=resume_at,
            configured_budget_usd=(
                float(configured) if isinstance(configured, (int, float)) else None
            ),
            current_cost_usd=(
                float(current) if isinstance(current, (int, float)) else None
            ),
        )

    # ─── Result Handling ──────────────────────────────────────────────

    def handle_execution_result(
        self,
        waypoint: Waypoint,
        result: ExecutionResult,
        git_config: "GitConfig | None" = None,
    ) -> NextAction:
        """Handle the result of waypoint execution.

        Updates waypoint status, checks parent completion, commits if
        configured, and determines next action.

        Args:
            waypoint: The waypoint that was executed
            result: The execution result
            git_config: Optional git config for commit behavior.
                If None, commit uses the coordinator's git service with defaults.

        Returns:
            NextAction indicating what UI should do next
        """
        if result == ExecutionResult.SUCCESS:
            waypoint.status = WaypointStatus.COMPLETE
            waypoint.completed_at = datetime.now(UTC)
            self._coord.save_flight_plan()

            # Check if parent epic should auto-complete
            self.check_parent_completion(waypoint)

            # Commit waypoint changes
            commit_result = self.commit_waypoint(waypoint, git_config)

            next_action = build_next_action_after_success(self._coord.flight_plan)
            next_action.commit_result = commit_result
            self._coord.current_waypoint = (
                next_action.waypoint if next_action.action == "continue" else None
            )
            return next_action

        elif result == ExecutionResult.CANCELLED:
            waypoint.status = WaypointStatus.PENDING
            self._coord.save_flight_plan()
            return NextAction(action="pause", message="Execution cancelled")

        elif result == ExecutionResult.FAILED:
            waypoint.status = WaypointStatus.FAILED
            self._coord.save_flight_plan()
            return NextAction(
                action="intervention",
                waypoint=waypoint,
                message=f"Waypoint {waypoint.id} failed",
            )

        elif result == ExecutionResult.MAX_ITERATIONS:
            waypoint.status = WaypointStatus.FAILED
            self._coord.save_flight_plan()
            return NextAction(
                action="intervention",
                waypoint=waypoint,
                message=f"Waypoint {waypoint.id} hit max iterations without completion",
            )

        else:
            # INTERVENTION_NEEDED
            waypoint.status = WaypointStatus.FAILED
            self._coord.save_flight_plan()
            return NextAction(
                action="intervention",
                waypoint=waypoint,
                message=f"Waypoint {waypoint.id} needs human intervention",
            )

    def check_parent_completion(self, waypoint: Waypoint) -> None:
        """Check if parent epic is ready for execution (all children done).

        This method logs when a parent becomes ready but does NOT auto-complete.
        Parents will be selected and executed like regular waypoints to verify
        their own acceptance criteria.

        Args:
            waypoint: The child waypoint that just completed
        """
        if self._coord.flight_plan is None or waypoint.parent_id is None:
            return

        parent = self._coord.flight_plan.get_waypoint(waypoint.parent_id)
        if parent is None or parent.status == WaypointStatus.COMPLETE:
            return

        # Check if all children are complete or skipped
        children = self._coord.flight_plan.get_children(parent.id)
        all_done = all(
            c.status in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED)
            for c in children
        )

        if all_done:
            logger.info(
                "Parent epic %s ready for execution (all children done)", parent.id
            )
            # Don't auto-complete - parent will be selected and executed
            # to verify its own acceptance criteria

    # ─── Intervention Handling ────────────────────────────────────────

    def handle_intervention(
        self,
        intervention: Intervention,
        action: InterventionAction,
        additional_iterations: int = 5,
        rollback_tag: str | None = None,
    ) -> NextAction:
        """Handle user's response to an intervention.

        Args:
            intervention: The intervention that was shown
            action: User's chosen action
            additional_iterations: Extra iterations for RETRY
            rollback_tag: Git tag for ROLLBACK

        Returns:
            NextAction indicating what UI should do next
        """
        new_status, next_action = build_intervention_resolution(
            flight_plan=self._coord.flight_plan,
            intervention=intervention,
            action=action,
            additional_iterations=additional_iterations,
            rollback_tag=rollback_tag,
        )

        if new_status is not None:
            intervention.waypoint.status = new_status
            self._coord.save_flight_plan()

        self._coord.current_waypoint = (
            next_action.waypoint if next_action.action == "continue" else None
        )
        return next_action

    # ─── Completion Status ────────────────────────────────────────────

    def get_completion_status(self) -> CompletionStatus:
        """Get summary of waypoint completion state."""
        return build_completion_status(self._coord.flight_plan)

    # ─── Git Integration ──────────────────────────────────────────────

    def commit_waypoint(
        self,
        waypoint: Waypoint,
        git_config: "GitConfig | None" = None,
    ) -> CommitResult:
        """Commit waypoint changes to git via extracted policy helper."""
        return commit_waypoint_with_policy(
            self._coord.project,
            waypoint,
            git_config=git_config,
            git_service=self._coord.git,
        )

    def rollback_to_tag(self, tag: str | None) -> RollbackResult:
        """Rollback git to the specified tag and reload the flight plan.

        Args:
            tag: Git tag to reset to, or None for manual rollback.

        Returns:
            RollbackResult with success status, message, and reloaded plan.
        """
        result = rollback_to_tag_with_policy(
            self._coord.project,
            tag,
            git_service=self._coord.git,
        )
        if result.success and result.flight_plan is not None:
            self._coord.flight_plan = result.flight_plan
        return result
