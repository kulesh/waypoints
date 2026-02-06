"""FLY phase delegate — waypoint selection, execution, result handling, git.

Owns the business logic for executing waypoints: selecting the next
waypoint, driving the executor, handling results, checking parent
completion, managing interventions, and committing to git.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from waypoints.fly.executor import ExecutionContext, ExecutionResult, WaypointExecutor
from waypoints.fly.intervention import Intervention, InterventionAction
from waypoints.models import Waypoint, WaypointStatus
from waypoints.orchestration.types import (
    CommitResult,
    CompletionStatus,
    NextAction,
    ProgressCallback,
    ProgressUpdate,
)

if TYPE_CHECKING:
    from waypoints.git.config import GitConfig
    from waypoints.orchestration.coordinator import JourneyCoordinator

logger = logging.getLogger(__name__)


class FlyPhase:
    """Waypoint selection, execution, result handling, and git integration."""

    def __init__(self, coordinator: "JourneyCoordinator") -> None:
        self._coord = coordinator

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
        if self._coord.flight_plan is None:
            return None

        # Phase 1: Check for resumable waypoints (failed/in-progress)
        if include_failed:
            for wp in self._coord.flight_plan.waypoints:
                if wp.status in (WaypointStatus.IN_PROGRESS, WaypointStatus.FAILED):
                    logger.info("Resuming %s waypoint: %s", wp.status.value, wp.id)
                    self._coord.current_waypoint = wp
                    return wp

        # Phase 2: Find next pending waypoint with met dependencies
        for wp in self._coord.flight_plan.waypoints:
            if wp.status != WaypointStatus.PENDING:
                continue

            # Epics can only run after all children complete
            if self._coord.flight_plan.is_epic(wp.id):
                children = self._coord.flight_plan.get_children(wp.id)
                all_children_done = all(
                    c.status in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED)
                    for c in children
                )
                if not all_children_done:
                    continue  # Skip until children are done
                # Fall through - parent is ready for execution

            # Check dependencies
            if not self._dependencies_met(wp):
                continue

            logger.info("Selected next waypoint: %s", wp.id)
            self._coord.current_waypoint = wp
            return wp

        logger.info("No waypoints available to execute")
        self._coord.current_waypoint = None
        return None

    def _dependencies_met(self, waypoint: Waypoint) -> bool:
        """Check if all dependencies are satisfied (COMPLETE or SKIPPED)."""
        if self._coord.flight_plan is None:
            return False

        for dep_id in waypoint.dependencies:
            dep = self._coord.flight_plan.get_waypoint(dep_id)
            if dep is None:
                logger.warning("Dependency %s not found for %s", dep_id, waypoint.id)
                return False
            if dep.status not in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED):
                return False

        return True

    # ─── Execution ────────────────────────────────────────────────────

    async def execute_waypoint(
        self,
        waypoint: Waypoint,
        on_progress: ProgressCallback | None = None,
        max_iterations: int = 10,
        host_validations_enabled: bool = True,
    ) -> ExecutionResult:
        """Execute a waypoint using the AI executor.

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

        # Get product spec for context
        spec = self._coord.product_spec

        # Create executor
        executor = WaypointExecutor(
            project=self._coord.project,
            waypoint=waypoint,
            spec=spec,
            on_progress=self._wrap_progress_callback(on_progress),
            max_iterations=max_iterations,
            metrics_collector=self._coord.metrics,
            host_validations_enabled=host_validations_enabled,
        )

        # Execute (may raise InterventionNeededError)
        return await executor.execute()

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

            # Find next waypoint
            next_wp = self.select_next_waypoint()
            if next_wp:
                return NextAction(
                    action="continue",
                    waypoint=next_wp,
                    commit_result=commit_result,
                )
            else:
                return NextAction(
                    action="complete",
                    message="All waypoints complete!",
                    commit_result=commit_result,
                )

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
        waypoint = intervention.waypoint

        if action == InterventionAction.RETRY:
            # Reset to in-progress and retry
            waypoint.status = WaypointStatus.IN_PROGRESS
            self._coord.save_flight_plan()
            return NextAction(
                action="continue",
                waypoint=waypoint,
                message=f"Retrying with {additional_iterations} more iterations",
            )

        elif action == InterventionAction.SKIP:
            # Mark as skipped and continue
            waypoint.status = WaypointStatus.SKIPPED
            self._coord.save_flight_plan()
            next_wp = self.select_next_waypoint()
            if next_wp:
                return NextAction(action="continue", waypoint=next_wp)
            else:
                return NextAction(action="complete")

        elif action == InterventionAction.ROLLBACK:
            # Rollback to tag and pause
            # TODO: Implement rollback when GitService supports it
            waypoint.status = WaypointStatus.PENDING
            self._coord.save_flight_plan()
            return NextAction(action="pause", message=f"Rolled back to {rollback_tag}")

        elif action == InterventionAction.ABORT:
            # Mark failed and stop
            waypoint.status = WaypointStatus.FAILED
            self._coord.save_flight_plan()
            return NextAction(action="abort", message="Execution aborted")

        elif action == InterventionAction.WAIT:
            # Keep waypoint resumable and pause until external budget reset
            waypoint.status = WaypointStatus.PENDING
            self._coord.save_flight_plan()
            return NextAction(action="pause", message="Paused waiting for budget reset")

        elif action == InterventionAction.EDIT:
            # Return to CHART for editing
            return NextAction(action="pause", message="Edit waypoint and retry")

        return NextAction(action="pause")

    # ─── Completion Status ────────────────────────────────────────────

    def get_completion_status(self) -> CompletionStatus:
        """Get summary of waypoint completion state."""
        if self._coord.flight_plan is None:
            return CompletionStatus(total=0, complete=0, pending=0, failed=0, blocked=0)

        total = 0
        complete = 0
        pending = 0
        failed = 0
        blocked = 0
        in_progress = 0

        for wp in self._coord.flight_plan.waypoints:
            total += 1

            if wp.status == WaypointStatus.COMPLETE:
                complete += 1
            elif wp.status == WaypointStatus.SKIPPED:
                complete += 1  # Count skipped as "done"
            elif wp.status == WaypointStatus.FAILED:
                failed += 1
            elif wp.status == WaypointStatus.IN_PROGRESS:
                in_progress += 1
            elif wp.status == WaypointStatus.PENDING:
                # Check if blocked by failed dependency
                is_blocked = False
                for dep_id in wp.dependencies:
                    dep = self._coord.flight_plan.get_waypoint(dep_id)
                    if dep is not None and dep.status == WaypointStatus.FAILED:
                        is_blocked = True
                        break
                if is_blocked:
                    blocked += 1
                else:
                    pending += 1

        return CompletionStatus(
            total=total,
            complete=complete,
            pending=pending,
            failed=failed,
            blocked=blocked,
            in_progress=in_progress,
        )

    # ─── Git Integration ──────────────────────────────────────────────

    def commit_waypoint(
        self,
        waypoint: Waypoint,
        git_config: "GitConfig | None" = None,
    ) -> CommitResult:
        """Commit waypoint changes to git.

        Uses GitConfig to determine auto-commit, auto-init, receipt
        validation, staging method, and tag creation. This is the single
        source of truth for waypoint commits — both TUI and headless
        callers use this method.

        Args:
            waypoint: The completed waypoint to commit
            git_config: Git configuration. If None, loads from project.

        Returns:
            CommitResult describing what happened
        """
        from waypoints.git.config import GitConfig as GitConfigClass
        from waypoints.git.service import GitService

        config = git_config or GitConfigClass.load(self._coord.project.slug)
        project_path = self._coord.project.get_path()

        if not config.auto_commit:
            return CommitResult(committed=False, message="Auto-commit disabled")

        # Get or create git service
        git = self._coord.git or GitService(project_path)

        # Auto-init if needed
        initialized = False
        if not git.is_git_repo():
            if config.auto_init:
                init_result = git.init_repo()
                if not init_result.success:
                    return CommitResult(
                        committed=False,
                        message=f"Failed to init git repo: {init_result.message}",
                    )
                initialized = True
            else:
                return CommitResult(
                    committed=False,
                    message="Not a git repo and auto-init disabled",
                )

        # Validate receipt if configured
        if config.run_checklist:
            from waypoints.git.receipt import ReceiptValidator

            validator = ReceiptValidator()
            receipt_path = validator.find_latest_receipt(
                self._coord.project, waypoint.id
            )

            if receipt_path is None:
                return CommitResult(
                    committed=False,
                    message=f"No receipt found for {waypoint.id}",
                )

            validation = validator.validate(receipt_path)
            if not validation.valid:
                return CommitResult(
                    committed=False,
                    message=f"Receipt invalid: {validation.message}",
                )

        # Stage project files (config-aware staging)
        slug = self._coord.project.slug
        git.stage_project_files(slug)

        # Commit
        commit_msg = f"feat({slug}): Complete {waypoint.title}"
        result = git.commit(commit_msg)

        if not result.success:
            if "Nothing to commit" in result.message:
                return CommitResult(
                    committed=False,
                    message="Nothing to commit",
                    initialized_repo=initialized,
                )
            return CommitResult(
                committed=False,
                message=f"Commit failed: {result.message}",
                initialized_repo=initialized,
            )

        commit_hash = git.get_head_commit()

        # Create tag if configured
        tag_name = None
        if config.create_waypoint_tags:
            tag_name = f"{slug}/{waypoint.id}"
            git.tag(tag_name, f"Completed waypoint: {waypoint.title}")

        logger.info("Committed waypoint %s: %s", waypoint.id, commit_msg)
        return CommitResult(
            committed=True,
            message=commit_msg,
            commit_hash=commit_hash,
            tag_name=tag_name,
            initialized_repo=initialized,
        )
