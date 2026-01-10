"""Journey coordinator - orchestrates business logic independent of UI.

This module extracts business logic from TUI screens into a testable,
reusable coordinator class. Screens become thin wrappers that call
coordinator methods and render results.

Benefits:
- Testable without TUI
- Enables headless/CI mode
- Clear separation of concerns
- Single source of truth for journey state
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from waypoints.fly.executor import (
    ExecutionContext,
    ExecutionResult,
    WaypointExecutor,
)
from waypoints.fly.intervention import Intervention, InterventionAction
from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint, WaypointStatus
from waypoints.orchestration.types import (
    ChunkCallback,
    CompletionStatus,
    NextAction,
    ProgressCallback,
    ProgressUpdate,
    TextStream,
)

if TYPE_CHECKING:
    from waypoints.git.service import GitService
    from waypoints.llm.client import ChatClient
    from waypoints.llm.metrics import MetricsCollector
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)


class JourneyCoordinator:
    """Coordinates journey phases independent of UI.

    This class owns all business logic for:
    - FLY phase: waypoint selection, execution, completion
    - CHART phase: flight plan generation, waypoint CRUD
    - IDEATION phase: Q&A dialogue management

    Screens call coordinator methods and render results.
    The coordinator manages state and persistence.
    """

    def __init__(
        self,
        project: "Project",
        flight_plan: FlightPlan | None = None,
        llm: "ChatClient | None" = None,
        git: "GitService | None" = None,
        metrics: "MetricsCollector | None" = None,
    ) -> None:
        """Initialize coordinator with project and services.

        Args:
            project: The project being worked on
            flight_plan: Optional pre-loaded flight plan (avoids reload from disk)
            llm: LLM client for AI operations (optional for testing)
            git: Git service for version control (optional for testing)
            metrics: Metrics collector for cost tracking (optional)
        """
        self.project = project
        self.llm = llm
        self.git = git
        self.metrics = metrics
        self._flight_plan: FlightPlan | None = flight_plan
        self._current_waypoint: Waypoint | None = None

    # ─── Properties ──────────────────────────────────────────────────────

    @property
    def flight_plan(self) -> FlightPlan | None:
        """Get the current flight plan, loading if necessary."""
        if self._flight_plan is None:
            self._flight_plan = self._load_flight_plan()
        return self._flight_plan

    @property
    def current_waypoint(self) -> Waypoint | None:
        """Get the currently selected waypoint."""
        return self._current_waypoint

    @current_waypoint.setter
    def current_waypoint(self, waypoint: Waypoint | None) -> None:
        """Set the currently selected waypoint."""
        self._current_waypoint = waypoint

    def is_epic(self, waypoint_id: str) -> bool:
        """Check if a waypoint is an epic (has children)."""
        if self.flight_plan is None:
            return False
        return self.flight_plan.is_epic(waypoint_id)

    def reset_stale_in_progress(self) -> bool:
        """Reset any stale IN_PROGRESS waypoints to PENDING.

        Called on session start to clean up state from crashed/killed sessions.

        Returns:
            True if any waypoints were reset, False otherwise.
        """
        if self.flight_plan is None:
            return False

        changed = False
        for wp in self.flight_plan.waypoints:
            if wp.status == WaypointStatus.IN_PROGRESS:
                wp.status = WaypointStatus.PENDING
                changed = True
                logger.info("Reset stale IN_PROGRESS waypoint %s to PENDING", wp.id)

        if changed:
            self._save_flight_plan()
        return changed

    def mark_waypoint_status(self, waypoint: Waypoint, status: WaypointStatus) -> None:
        """Mark a waypoint with a new status and save.

        Args:
            waypoint: The waypoint to update
            status: The new status
        """
        waypoint.status = status
        self._save_flight_plan()

    # ─── FLY Phase: Waypoint Selection ───────────────────────────────────

    def select_next_waypoint(
        self,
        include_failed: bool = False,
    ) -> Waypoint | None:
        """Find the next waypoint to execute.

        Selection logic:
        1. If include_failed, check for IN_PROGRESS or FAILED waypoints first
        2. Find first PENDING waypoint with all dependencies met
        3. Skip epics (they complete when all children complete)

        Args:
            include_failed: Whether to consider failed/in-progress waypoints

        Returns:
            Next waypoint to execute, or None if all complete/blocked
        """
        if self.flight_plan is None:
            return None

        # Phase 1: Check for resumable waypoints (failed/in-progress)
        if include_failed:
            for wp in self.flight_plan.waypoints:
                if wp.status in (WaypointStatus.IN_PROGRESS, WaypointStatus.FAILED):
                    logger.info("Resuming %s waypoint: %s", wp.status.value, wp.id)
                    self._current_waypoint = wp
                    return wp

        # Phase 2: Find next pending waypoint with met dependencies
        for wp in self.flight_plan.waypoints:
            if wp.status != WaypointStatus.PENDING:
                continue

            # Skip epics - they auto-complete when children complete
            if self.flight_plan.is_epic(wp.id):
                continue

            # Check dependencies
            if not self._dependencies_met(wp):
                continue

            logger.info("Selected next waypoint: %s", wp.id)
            self._current_waypoint = wp
            return wp

        logger.info("No waypoints available to execute")
        self._current_waypoint = None
        return None

    def _dependencies_met(self, waypoint: Waypoint) -> bool:
        """Check if all dependencies are satisfied (COMPLETE or SKIPPED)."""
        if self.flight_plan is None:
            return False

        for dep_id in waypoint.dependencies:
            dep = self.flight_plan.get_waypoint(dep_id)
            if dep is None:
                logger.warning("Dependency %s not found for %s", dep_id, waypoint.id)
                return False
            if dep.status not in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED):
                return False

        return True

    # ─── FLY Phase: Execution ────────────────────────────────────────────

    async def execute_waypoint(
        self,
        waypoint: Waypoint,
        on_progress: ProgressCallback | None = None,
        max_iterations: int = 10,
    ) -> ExecutionResult:
        """Execute a waypoint using the AI executor.

        Args:
            waypoint: The waypoint to execute
            on_progress: Callback for progress updates
            max_iterations: Maximum execution iterations

        Returns:
            ExecutionResult indicating success/failure

        Raises:
            InterventionNeededError: When human intervention is required
        """
        # Mark as in progress
        waypoint.status = WaypointStatus.IN_PROGRESS
        self._save_flight_plan()

        # Get product spec for context
        spec = self._load_product_spec()

        # Create executor
        executor = WaypointExecutor(
            project=self.project,
            waypoint=waypoint,
            spec=spec,
            on_progress=self._wrap_progress_callback(on_progress),
            max_iterations=max_iterations,
            metrics_collector=self.metrics,
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

    # ─── FLY Phase: Result Handling ──────────────────────────────────────

    def handle_execution_result(
        self,
        waypoint: Waypoint,
        result: ExecutionResult,
    ) -> NextAction:
        """Handle the result of waypoint execution.

        Updates waypoint status, checks parent completion,
        and determines next action.

        Args:
            waypoint: The waypoint that was executed
            result: The execution result

        Returns:
            NextAction indicating what UI should do next
        """
        if result == ExecutionResult.SUCCESS:
            waypoint.status = WaypointStatus.COMPLETE
            self._save_flight_plan()

            # Check if parent epic should auto-complete
            self._check_parent_completion(waypoint)

            # Commit if git is available
            if self.git:
                self._commit_waypoint(waypoint)

            # Find next waypoint
            next_wp = self.select_next_waypoint()
            if next_wp:
                return NextAction(action="continue", waypoint=next_wp)
            else:
                return NextAction(action="complete", message="All waypoints complete!")

        elif result == ExecutionResult.FAILED:
            waypoint.status = WaypointStatus.FAILED
            self._save_flight_plan()
            return NextAction(
                action="pause",
                waypoint=waypoint,
                message=f"Waypoint {waypoint.id} failed",
            )

        elif result == ExecutionResult.CANCELLED:
            waypoint.status = WaypointStatus.PENDING
            self._save_flight_plan()
            return NextAction(action="pause", message="Execution cancelled")

        else:
            # MAX_ITERATIONS or INTERVENTION_NEEDED handled via exception
            return NextAction(action="pause", waypoint=waypoint)

    def _check_parent_completion(self, waypoint: Waypoint) -> None:
        """Check if parent epic should auto-complete when child completes.

        Recursively checks up the tree - if all siblings complete,
        parent completes, which may trigger grandparent completion, etc.
        """
        if self.flight_plan is None or waypoint.parent_id is None:
            return

        parent = self.flight_plan.get_waypoint(waypoint.parent_id)
        if parent is None:
            return

        # Check if all children are complete or skipped
        children = self.flight_plan.get_children(parent.id)
        all_done = all(
            c.status in (WaypointStatus.COMPLETE, WaypointStatus.SKIPPED)
            for c in children
        )

        if all_done and parent.status != WaypointStatus.COMPLETE:
            logger.info("Auto-completing parent epic: %s", parent.id)
            parent.status = WaypointStatus.COMPLETE
            self._save_flight_plan()

            # Recursively check grandparent
            self._check_parent_completion(parent)

    # ─── FLY Phase: Intervention Handling ────────────────────────────────

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
            self._save_flight_plan()
            return NextAction(
                action="continue",
                waypoint=waypoint,
                message=f"Retrying with {additional_iterations} more iterations",
            )

        elif action == InterventionAction.SKIP:
            # Mark as skipped and continue
            waypoint.status = WaypointStatus.SKIPPED
            self._save_flight_plan()
            next_wp = self.select_next_waypoint()
            if next_wp:
                return NextAction(action="continue", waypoint=next_wp)
            else:
                return NextAction(action="complete")

        elif action == InterventionAction.ROLLBACK:
            # Rollback to tag and pause
            # TODO: Implement rollback when GitService supports it
            # if self.git and rollback_tag:
            #     self.git.rollback_to_tag(rollback_tag)
            waypoint.status = WaypointStatus.PENDING
            self._save_flight_plan()
            return NextAction(action="pause", message=f"Rolled back to {rollback_tag}")

        elif action == InterventionAction.ABORT:
            # Mark failed and stop
            waypoint.status = WaypointStatus.FAILED
            self._save_flight_plan()
            return NextAction(action="abort", message="Execution aborted")

        elif action == InterventionAction.EDIT:
            # Return to CHART for editing
            return NextAction(action="pause", message="Edit waypoint and retry")

        return NextAction(action="pause")

    # ─── FLY Phase: Completion Status ────────────────────────────────────

    def get_completion_status(self) -> CompletionStatus:
        """Get summary of waypoint completion state."""
        if self.flight_plan is None:
            return CompletionStatus(total=0, complete=0, pending=0, failed=0, blocked=0)

        total = 0
        complete = 0
        pending = 0
        failed = 0
        blocked = 0
        in_progress = 0

        for wp in self.flight_plan.waypoints:
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
                    dep = self.flight_plan.get_waypoint(dep_id)
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

    # ─── FLY Phase: Git Integration ──────────────────────────────────────

    def _commit_waypoint(self, waypoint: Waypoint) -> bool:
        """Commit waypoint changes to git.

        Validates receipt exists before committing.

        Returns:
            True if commit successful, False otherwise
        """
        if self.git is None:
            return False

        from waypoints.git.receipt import ReceiptValidator

        validator = ReceiptValidator()
        receipt_path = validator.find_latest_receipt(
            self.project.get_path(), waypoint.id
        )

        if receipt_path is None:
            logger.warning("No receipt found for %s, skipping commit", waypoint.id)
            return False

        result = validator.validate(receipt_path)
        if not result.valid:
            logger.warning("Receipt invalid for %s: %s", waypoint.id, result.message)
            return False

        # Create commit
        try:
            # Stage all changed files
            self.git.stage_files(".")
            commit_result = self.git.commit(f"feat({waypoint.id}): {waypoint.title}")
            if not commit_result.success:
                logger.warning(
                    "Commit failed for %s: %s", waypoint.id, commit_result.message
                )
                return False
            self.git.tag(f"waypoint/{waypoint.id}")
            logger.info("Committed waypoint: %s", waypoint.id)
            return True
        except Exception as e:
            logger.error("Failed to commit waypoint %s: %s", waypoint.id, e)
            return False

    # ─── CHART Phase: Flight Plan Generation ─────────────────────────────

    async def generate_flight_plan(
        self,
        spec: str,
        on_chunk: ChunkCallback | None = None,
    ) -> FlightPlan:
        """Generate flight plan from product specification.

        Args:
            spec: The product specification text
            on_chunk: Callback for streaming progress

        Returns:
            Generated FlightPlan
        """
        # TODO: Extract from ChartScreen._generate_waypoints()
        raise NotImplementedError("Will be extracted from ChartScreen")

    async def break_down_waypoint(
        self,
        waypoint: Waypoint,
        on_chunk: ChunkCallback | None = None,
    ) -> list[Waypoint]:
        """Break down a waypoint into sub-waypoints.

        Args:
            waypoint: The parent waypoint to break down
            on_chunk: Callback for streaming progress

        Returns:
            List of generated sub-waypoints
        """
        # TODO: Extract from ChartScreen._generate_sub_waypoints()
        raise NotImplementedError("Will be extracted from ChartScreen")

    def update_waypoint(self, waypoint: Waypoint) -> None:
        """Update a waypoint and persist changes."""
        if self.flight_plan is None:
            return
        self.flight_plan.update_waypoint(waypoint)
        self._save_flight_plan()

    def delete_waypoint(self, waypoint_id: str) -> list[str]:
        """Delete a waypoint and return IDs of orphaned dependents.

        Args:
            waypoint_id: ID of waypoint to delete

        Returns:
            List of waypoint IDs that had this as a dependency
        """
        # TODO: Extract from ChartScreen._delete_waypoint()
        raise NotImplementedError("Will be extracted from ChartScreen")

    # ─── IDEATION Phase: Q&A Dialogue ────────────────────────────────────

    async def start_qa_dialogue(self, idea: str) -> "TextStream":
        """Start the Q&A dialogue with an initial idea.

        Args:
            idea: The user's initial idea

        Yields:
            Text chunks of the first question
        """
        # TODO: Extract from IdeationQAScreen._start_qa()
        raise NotImplementedError("Will be extracted from IdeationQAScreen")

    async def continue_qa_dialogue(self, user_response: str) -> "TextStream":
        """Continue the Q&A dialogue with user's response.

        Args:
            user_response: User's answer to the previous question

        Yields:
            Text chunks of the next question
        """
        # TODO: Extract from IdeationQAScreen._send_to_llm()
        raise NotImplementedError("Will be extracted from IdeationQAScreen")

    # ─── Private Helpers ─────────────────────────────────────────────────

    def _load_flight_plan(self) -> FlightPlan | None:
        """Load flight plan from project."""
        try:
            from waypoints.models.flight_plan import FlightPlanReader

            return FlightPlanReader.load(self.project)
        except Exception as e:
            logger.warning("Could not load flight plan: %s", e)
            return None

    def _save_flight_plan(self) -> None:
        """Save flight plan to project."""
        if self.flight_plan is None:
            return
        try:
            from waypoints.models.flight_plan import FlightPlanWriter

            writer = FlightPlanWriter(self.project)
            writer.save(self.flight_plan)
        except Exception as e:
            logger.error("Failed to save flight plan: %s", e)

    def _load_product_spec(self) -> str:
        """Load product specification from project."""
        spec_path = self.project.get_path() / "docs" / "product-spec.md"
        if spec_path.exists():
            return spec_path.read_text()
        return ""
