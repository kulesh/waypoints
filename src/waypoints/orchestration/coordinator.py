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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoints.fly.executor import (
    ExecutionContext,
    ExecutionResult,
    WaypointExecutor,
)
from waypoints.fly.intervention import Intervention, InterventionAction
from waypoints.llm.client import ChatClient, StreamChunk
from waypoints.llm.prompts import (
    BRIEF_GENERATION_PROMPT,
    BRIEF_SUMMARY_PROMPT,
    BRIEF_SYSTEM_PROMPT,
    CHART_SYSTEM_PROMPT,
    QA_SYSTEM_PROMPT,
    REPRIORITIZE_PROMPT,
    SPEC_GENERATION_PROMPT,
    SPEC_SUMMARY_PROMPT,
    SPEC_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    WAYPOINT_ADD_PROMPT,
    WAYPOINT_BREAKDOWN_PROMPT,
    WAYPOINT_GENERATION_PROMPT,
)
from waypoints.llm.validation import (
    WaypointValidationError,
    validate_reprioritization,
    validate_single_waypoint,
    validate_waypoints,
)
from waypoints.models import (
    DialogueHistory,
    FlightPlan,
    JourneyState,
    JourneyStateManager,
    MessageRole,
    Project,
    SessionWriter,
    Waypoint,
    WaypointStatus,
)
from waypoints.orchestration.types import (
    ChunkCallback,
    CompletionStatus,
    NextAction,
    ProgressCallback,
    ProgressUpdate,
)

if TYPE_CHECKING:
    from waypoints.git.service import GitService
    from waypoints.llm.metrics import MetricsCollector

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
        project: Project,
        flight_plan: FlightPlan | None = None,
        llm: ChatClient | None = None,
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

        # Dialogue state for SHAPE phase
        self._dialogue_history: DialogueHistory | None = None
        self._session_writer: SessionWriter | None = None
        self._idea: str | None = None
        self._state_manager = JourneyStateManager(project)

    # ─── Properties ──────────────────────────────────────────────────────

    @property
    def flight_plan(self) -> FlightPlan | None:
        """Get the current flight plan, loading if necessary."""
        if self._flight_plan is None:
            self._flight_plan = self._load_flight_plan()
        return self._flight_plan

    @flight_plan.setter
    def flight_plan(self, value: FlightPlan | None) -> None:
        """Set the current flight plan."""
        self._flight_plan = value

    @property
    def current_waypoint(self) -> Waypoint | None:
        """Get the currently selected waypoint."""
        return self._current_waypoint

    @current_waypoint.setter
    def current_waypoint(self, waypoint: Waypoint | None) -> None:
        """Set the currently selected waypoint."""
        self._current_waypoint = waypoint

    def can_transition(self, target: JourneyState) -> bool:
        """Check if the project can transition to the target state."""
        return self._state_manager.is_transition_allowed(target)

    def transition(self, target: JourneyState, reason: str | None = None) -> None:
        """Transition the project journey to the target state."""
        self._state_manager.transition(target, reason=reason)

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
            self.save_flight_plan()
        return changed

    def mark_waypoint_status(self, waypoint: Waypoint, status: WaypointStatus) -> None:
        """Mark a waypoint with a new status and save.

        Args:
            waypoint: The waypoint to update
            status: The new status
        """
        waypoint.status = status
        self.save_flight_plan()

    # ─── FLY Phase: Waypoint Selection ───────────────────────────────────

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

            # Epics can only run after all children complete
            if self.flight_plan.is_epic(wp.id):
                children = self.flight_plan.get_children(wp.id)
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
        self.save_flight_plan()

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
            self.save_flight_plan()

            # Check if parent epic should auto-complete
            self.check_parent_completion(waypoint)

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
            self.save_flight_plan()
            return NextAction(
                action="pause",
                waypoint=waypoint,
                message=f"Waypoint {waypoint.id} failed",
            )

        elif result == ExecutionResult.CANCELLED:
            waypoint.status = WaypointStatus.PENDING
            self.save_flight_plan()
            return NextAction(action="pause", message="Execution cancelled")

        else:
            # MAX_ITERATIONS or INTERVENTION_NEEDED handled via exception
            return NextAction(action="pause", waypoint=waypoint)

    def check_parent_completion(self, waypoint: Waypoint) -> None:
        """Check if parent epic is ready for execution (all children done).

        This method logs when a parent becomes ready but does NOT auto-complete.
        Parents will be selected and executed like regular waypoints to verify
        their own acceptance criteria.

        Args:
            waypoint: The child waypoint that just completed
        """
        if self.flight_plan is None or waypoint.parent_id is None:
            return

        parent = self.flight_plan.get_waypoint(waypoint.parent_id)
        if parent is None or parent.status == WaypointStatus.COMPLETE:
            return

        # Check if all children are complete or skipped
        children = self.flight_plan.get_children(parent.id)
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
            self.save_flight_plan()
            return NextAction(
                action="continue",
                waypoint=waypoint,
                message=f"Retrying with {additional_iterations} more iterations",
            )

        elif action == InterventionAction.SKIP:
            # Mark as skipped and continue
            waypoint.status = WaypointStatus.SKIPPED
            self.save_flight_plan()
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
            self.save_flight_plan()
            return NextAction(action="pause", message=f"Rolled back to {rollback_tag}")

        elif action == InterventionAction.ABORT:
            # Mark failed and stop
            waypoint.status = WaypointStatus.FAILED
            self.save_flight_plan()
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
        receipt_path = validator.find_latest_receipt(self.project, waypoint.id)

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

    def generate_flight_plan(
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
        # Create LLM client if needed
        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="chart",
            )

        prompt = WAYPOINT_GENERATION_PROMPT.format(spec=spec)
        logger.info("Generating waypoints from spec: %d chars", len(spec))

        # Stream response from LLM
        full_response = ""
        for result in self.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=CHART_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Parse waypoints from response
        waypoints = self._parse_waypoints(full_response)

        # Create flight plan
        flight_plan = FlightPlan(waypoints=waypoints)
        self._flight_plan = flight_plan

        # Save to disk
        self.save_flight_plan()

        # Log initial generation to audit trail
        self._log_waypoint_event(
            "generated",
            {"waypoints": [wp.to_dict() for wp in waypoints]},
        )

        logger.info("Generated flight plan with %d waypoints", len(waypoints))
        return flight_plan

    def break_down_waypoint(
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

        Raises:
            ValueError: If waypoint is already an epic (has children)
        """
        if self.flight_plan and self.flight_plan.is_epic(waypoint.id):
            raise ValueError(f"{waypoint.id} already has sub-waypoints")

        # Create LLM client if needed
        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="chart",
            )

        # Format prompt
        criteria_str = "\n".join(f"- {c}" for c in waypoint.acceptance_criteria)
        if not criteria_str:
            criteria_str = "(none specified)"

        prompt = WAYPOINT_BREAKDOWN_PROMPT.format(
            parent_id=waypoint.id,
            title=waypoint.title,
            objective=waypoint.objective,
            criteria=criteria_str,
        )

        logger.info("Breaking down waypoint: %s", waypoint.id)

        # Stream response from LLM
        full_response = ""
        for result in self.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=CHART_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Parse sub-waypoints (pass existing IDs for validation)
        existing_ids = (
            {wp.id for wp in self.flight_plan.waypoints} if self.flight_plan else set()
        )
        sub_waypoints = self._parse_waypoints(full_response, existing_ids)

        # Ensure all have correct parent_id
        for wp in sub_waypoints:
            wp.parent_id = waypoint.id

        logger.info(
            "Generated %d sub-waypoints for %s", len(sub_waypoints), waypoint.id
        )
        return sub_waypoints

    def generate_waypoint(
        self,
        description: str,
        spec_summary: str | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> tuple[Waypoint, str | None]:
        """Generate a single waypoint from description.

        Args:
            description: User's description of what the waypoint should do
            spec_summary: Optional truncated product spec for context
            on_chunk: Callback for streaming progress

        Returns:
            Tuple of (waypoint, insert_after_id or None)

        Raises:
            WaypointValidationError: If generated waypoint fails validation
        """
        if self.flight_plan is None:
            raise RuntimeError("No flight plan loaded")

        # Create LLM client if needed
        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="chart",
            )

        next_id = self._next_waypoint_id()
        existing_ids = {wp.id for wp in self.flight_plan.waypoints}

        # Format existing waypoints for context
        existing_waypoints = "\n".join(
            f"- {wp.id}: {wp.title}" for wp in self.flight_plan.get_root_waypoints()
        )

        # Use provided spec_summary or empty string
        spec_context = spec_summary or "No product spec available"

        prompt = WAYPOINT_ADD_PROMPT.format(
            description=description,
            existing_waypoints=existing_waypoints or "No existing waypoints",
            spec_summary=spec_context,
            next_id=next_id,
        )

        logger.info("Generating waypoint from description: %s", description[:100])

        # Stream response from LLM
        full_response = ""
        for result in self.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=CHART_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Validate the response
        validation = validate_single_waypoint(full_response, existing_ids)
        if not validation.valid:
            raise WaypointValidationError(validation.errors)

        # Create waypoint from validated data
        data = validation.data
        assert data is not None
        waypoint = Waypoint(
            id=data["id"],
            title=data["title"],
            objective=data["objective"],
            acceptance_criteria=data.get("acceptance_criteria", []),
            dependencies=data.get("dependencies", []),
            status=WaypointStatus.PENDING,
        )

        logger.info("Generated waypoint: %s", waypoint.id)
        return waypoint, validation.insert_after

    def suggest_reprioritization(
        self,
        spec_summary: str | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> tuple[list[str], str, list[dict[str, str]]]:
        """Suggest optimal waypoint order.

        Args:
            spec_summary: Optional truncated product spec for context
            on_chunk: Callback for streaming progress

        Returns:
            Tuple of (new_order, rationale, changes) where:
            - new_order: List of waypoint IDs in suggested order
            - rationale: Explanation for the new order
            - changes: List of per-waypoint change reasons

        Raises:
            RuntimeError: If no flight plan or fewer than 2 waypoints
            WaypointValidationError: If reprioritization response invalid
        """
        import json

        if self.flight_plan is None:
            raise RuntimeError("No flight plan loaded")

        root_waypoints = self.flight_plan.get_root_waypoints()
        if len(root_waypoints) < 2:
            raise RuntimeError("Need at least 2 waypoints to reprioritize")

        # Create LLM client if needed
        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="chart",
            )

        # Format waypoints for context
        waypoints_json = json.dumps(
            [
                {"id": wp.id, "title": wp.title, "dependencies": wp.dependencies}
                for wp in root_waypoints
            ],
            indent=2,
        )

        spec_context = spec_summary or "No product spec available"

        prompt = REPRIORITIZE_PROMPT.format(
            waypoints_json=waypoints_json,
            spec_summary=spec_context,
        )

        logger.info("Generating reprioritization suggestion")

        # Stream response from LLM
        full_response = ""
        for result in self.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=CHART_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Validate response
        root_ids = {wp.id for wp in root_waypoints}
        validation = validate_reprioritization(full_response, root_ids)
        if not validation.valid:
            raise WaypointValidationError(validation.errors)

        logger.info("Reprioritization suggested: %s", validation.new_order)
        return validation.new_order, validation.rationale, validation.changes

    def _parse_waypoints(
        self, response: str, existing_ids: set[str] | None = None
    ) -> list[Waypoint]:
        """Parse and validate waypoints from LLM response.

        Args:
            response: Raw LLM response containing waypoint JSON
            existing_ids: Set of existing waypoint IDs (for sub-waypoint validation)

        Returns:
            List of validated Waypoint objects

        Raises:
            WaypointValidationError: If validation fails
        """
        result = validate_waypoints(response, existing_ids)

        if not result.valid:
            raise WaypointValidationError(result.errors)

        waypoints = []
        for item in result.data or []:
            wp = Waypoint(
                id=item["id"],
                title=item["title"],
                objective=item["objective"],
                acceptance_criteria=item.get("acceptance_criteria", []),
                parent_id=item.get("parent_id"),
                dependencies=item.get("dependencies", []),
                status=WaypointStatus.PENDING,
            )
            waypoints.append(wp)

        logger.info("Parsed %d waypoints from LLM response", len(waypoints))
        return waypoints

    def _next_waypoint_id(self) -> str:
        """Generate next available waypoint ID."""
        if self.flight_plan is None:
            return "WP-001"
        existing = {wp.id for wp in self.flight_plan.waypoints}
        for i in range(1, 1000):
            candidate = f"WP-{i:03d}"
            if candidate not in existing:
                return candidate
        return "WP-999"  # Fallback

    def update_waypoint(self, waypoint: Waypoint) -> None:
        """Update a waypoint and persist changes."""
        if self.flight_plan is None:
            return

        # Capture before state for audit log
        existing = self.flight_plan.get_waypoint(waypoint.id)
        before_data = existing.to_dict() if existing else {}

        self.flight_plan.update_waypoint(waypoint)
        self.save_flight_plan()

        # Log to audit trail
        self._log_waypoint_event(
            "updated",
            {
                "waypoint_id": waypoint.id,
                "before": before_data,
                "after": waypoint.to_dict(),
            },
        )

    def delete_waypoint(self, waypoint_id: str) -> list[str]:
        """Delete a waypoint and return IDs of orphaned dependents.

        Also removes any children if it's an epic.

        Args:
            waypoint_id: ID of waypoint to delete

        Returns:
            List of waypoint IDs that had this as a dependency
        """
        if self.flight_plan is None:
            return []

        # Capture waypoint data before deletion for audit log
        waypoint = self.flight_plan.get_waypoint(waypoint_id)
        waypoint_data = waypoint.to_dict() if waypoint else {}

        # Get dependents before deletion
        dependents = self.flight_plan.get_dependents(waypoint_id)
        dependent_ids = [wp.id for wp in dependents]

        # Remove the waypoint (FlightPlan handles children)
        self.flight_plan.remove_waypoint(waypoint_id)

        # Save to disk
        self.save_flight_plan()

        # Log to audit trail
        self._log_waypoint_event(
            "deleted",
            {
                "waypoint_id": waypoint_id,
                "waypoint": waypoint_data,
            },
        )

        logger.info(
            "Deleted waypoint %s (orphaned %d dependents)",
            waypoint_id,
            len(dependent_ids),
        )
        return dependent_ids

    def add_sub_waypoints(self, parent_id: str, sub_waypoints: list[Waypoint]) -> None:
        """Add sub-waypoints to a parent waypoint.

        Inserts after parent to maintain tree order.

        Args:
            parent_id: ID of the parent waypoint
            sub_waypoints: List of child waypoints to add
        """
        if self.flight_plan is None:
            return

        # Ensure all have correct parent_id
        for wp in sub_waypoints:
            wp.parent_id = parent_id

        # Insert after parent
        self.flight_plan.insert_waypoints_after(parent_id, sub_waypoints)

        # Save to disk
        self.save_flight_plan()

        # Log to audit trail
        self._log_waypoint_event(
            "broken_down",
            {
                "parent_id": parent_id,
                "sub_waypoints": [wp.to_dict() for wp in sub_waypoints],
            },
        )

        logger.info("Added %d sub-waypoints to %s", len(sub_waypoints), parent_id)

    def add_waypoint(self, waypoint: Waypoint, after_id: str | None = None) -> None:
        """Add a new waypoint to the flight plan.

        Args:
            waypoint: The waypoint to add
            after_id: Insert after this waypoint ID. If None, append to end.
        """
        if self.flight_plan is None:
            return

        if after_id:
            self.flight_plan.insert_waypoint_at(waypoint, after_id)
        else:
            self.flight_plan.add_waypoint(waypoint)

        self.save_flight_plan()

        # Log to audit trail
        self._log_waypoint_event(
            "added",
            {
                "waypoint": waypoint.to_dict(),
                "insert_after": after_id,
            },
        )

        logger.info("Added waypoint %s (after %s)", waypoint.id, after_id or "end")

    def reorder_waypoints(
        self,
        new_order: list[str],
        rationale: str = "",
        changes: list[dict[str, str]] | None = None,
    ) -> None:
        """Reorder root waypoints and log the change.

        Args:
            new_order: List of root waypoint IDs in the new order
            rationale: AI's explanation for the new order
            changes: Optional list of per-waypoint change reasons
        """
        if self.flight_plan is None:
            return

        # Capture previous order for audit log
        previous_order = [wp.id for wp in self.flight_plan.get_root_waypoints()]

        # Reorder
        self.flight_plan.reorder_waypoints(new_order)
        self.save_flight_plan()

        # Log to audit trail
        self._log_waypoint_event(
            "reprioritized",
            {
                "previous_order": previous_order,
                "new_order": new_order,
                "rationale": rationale,
                "changes": changes or [],
            },
        )

        prev_summary = " -> ".join(previous_order[:3])
        new_summary = " -> ".join(new_order[:3])
        if len(previous_order) > 3:
            prev_summary += "..."
        if len(new_order) > 3:
            new_summary += "..."
        logger.info("Reordered waypoints: %s -> %s", prev_summary, new_summary)

    # ─── IDEATION Phase: Q&A Dialogue ────────────────────────────────────

    @property
    def dialogue_history(self) -> DialogueHistory | None:
        """Get the current dialogue history."""
        return self._dialogue_history

    def start_qa_dialogue(
        self,
        idea: str,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Start Q&A dialogue with first question.

        Initializes dialogue history, sends idea to LLM,
        and returns the first question.

        Args:
            idea: The user's initial idea
            on_chunk: Optional callback for streaming chunks

        Returns:
            The LLM's first question/response
        """
        # Initialize dialogue state
        self._idea = idea
        self._dialogue_history = DialogueHistory()
        self._session_writer = SessionWriter(
            self.project, "ideation", self._dialogue_history.session_id
        )

        # Create LLM client if needed
        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="ideation-qa",
            )

        # Format initial context
        initial_context = (
            f"I have an idea I'd like to refine:\n\n{idea}\n\n"
            "Please help me crystallize this idea by asking clarifying questions."
        )

        # Add to history and persist
        initial_msg = self._dialogue_history.add_message(
            MessageRole.USER, initial_context
        )
        self._session_writer.append_message(initial_msg)

        logger.info("Starting ideation Q&A with idea: %s", idea[:100])

        # Stream response from LLM
        response_content = ""
        for result in self.llm.stream_message(
            messages=self._dialogue_history.to_api_format(),
            system=QA_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                response_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save assistant response to history
        assistant_msg = self._dialogue_history.add_message(
            MessageRole.ASSISTANT, response_content
        )
        self._session_writer.append_message(assistant_msg)

        return response_content

    def continue_qa_dialogue(
        self,
        user_response: str,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Continue Q&A dialogue with user's response.

        Args:
            user_response: User's answer to the previous question
            on_chunk: Optional callback for streaming chunks

        Returns:
            The LLM's next question/response

        Raises:
            RuntimeError: If dialogue not started (call start_qa_dialogue first)
        """
        if self._dialogue_history is None or self._session_writer is None:
            raise RuntimeError("Dialogue not started. Call start_qa_dialogue first.")

        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="ideation-qa",
            )

        # Add user response to history
        user_msg = self._dialogue_history.add_message(MessageRole.USER, user_response)
        self._session_writer.append_message(user_msg)

        # Stream response from LLM
        response_content = ""
        for result in self.llm.stream_message(
            messages=self._dialogue_history.to_api_format(),
            system=QA_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                response_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save assistant response to history
        assistant_msg = self._dialogue_history.add_message(
            MessageRole.ASSISTANT, response_content
        )
        self._session_writer.append_message(assistant_msg)

        return response_content

    # ─── SHAPE Phase: Brief & Spec Generation ────────────────────────────

    def generate_idea_brief(
        self,
        history: DialogueHistory | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Generate idea brief from Q&A dialogue.

        Args:
            history: Dialogue history to use (defaults to current session)
            on_chunk: Optional callback for streaming chunks

        Returns:
            The generated idea brief content

        Raises:
            RuntimeError: If no dialogue history available
        """
        dialogue = history or self._dialogue_history
        if dialogue is None:
            raise RuntimeError("No dialogue history. Run Q&A dialogue first.")

        # Create LLM client if needed
        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="idea-brief",
            )

        # Format conversation for prompt
        conversation_text = self._format_conversation(dialogue)
        prompt = BRIEF_GENERATION_PROMPT.format(conversation=conversation_text)

        logger.info("Generating idea brief from %d messages", len(dialogue.messages))

        # Stream response from LLM
        brief_content = ""
        for result in self.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=BRIEF_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                brief_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save to disk
        file_path = self._save_doc("idea-brief", brief_content)
        logger.info("Saved brief to %s", file_path)

        # Generate summary in background (non-blocking for now)
        self._generate_project_summary(brief_content, "brief")

        return brief_content

    def generate_product_spec(
        self,
        brief: str,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Generate product specification from idea brief.

        Args:
            brief: The idea brief content
            on_chunk: Optional callback for streaming chunks

        Returns:
            The generated product specification content
        """
        # Create LLM client if needed
        if self.llm is None:
            self.llm = ChatClient(
                metrics_collector=self.metrics,
                phase="product-spec",
            )

        prompt = SPEC_GENERATION_PROMPT.format(brief=brief)

        logger.info("Generating product spec from brief: %d chars", len(brief))

        # Stream response from LLM
        spec_content = ""
        for result in self.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=SPEC_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                spec_content += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Save to disk
        file_path = self._save_doc("product-spec", spec_content)
        logger.info("Saved spec to %s", file_path)

        # Generate summary
        self._generate_project_summary(spec_content, "spec")

        return spec_content

    def _format_conversation(self, history: DialogueHistory) -> str:
        """Format dialogue history for generation prompts."""
        parts = []
        for msg in history.messages:
            role = "User" if msg.role == MessageRole.USER else "Assistant"
            parts.append(f"{role}: {msg.content}")
        return "\n\n".join(parts)

    def _save_doc(self, doc_type: str, content: str) -> Path:
        """Save document to project docs directory.

        Args:
            doc_type: Type of document (e.g., "idea-brief", "product-spec")
            content: Document content

        Returns:
            Path to saved file
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        docs_dir = self.project.get_docs_path()
        docs_dir.mkdir(parents=True, exist_ok=True)
        file_path = docs_dir / f"{doc_type}-{timestamp}.md"
        file_path.write_text(content)
        return file_path

    def _generate_project_summary(self, content: str, source: str) -> None:
        """Generate and save project summary from content.

        Args:
            content: The document content to summarize
            source: Source type ("brief" or "spec")
        """
        if self.llm is None:
            return

        if source == "brief":
            prompt = BRIEF_SUMMARY_PROMPT.format(brief_content=content)
        else:
            prompt = SPEC_SUMMARY_PROMPT.format(spec_content=content)

        try:
            summary = ""
            for result in self.llm.stream_message(
                messages=[{"role": "user", "content": prompt}],
                system=SUMMARY_SYSTEM_PROMPT,
            ):
                if isinstance(result, StreamChunk):
                    summary += result.text

            # Clean up and save
            summary = summary.strip()
            self.project.summary = summary
            self.project.save()
            logger.info("Generated project summary: %d chars", len(summary))
        except Exception as e:
            logger.exception("Error generating summary: %s", e)

    # ─── Private Helpers ─────────────────────────────────────────────────

    def _load_flight_plan(self) -> FlightPlan | None:
        """Load flight plan from project."""
        try:
            from waypoints.models.flight_plan import FlightPlanReader

            return FlightPlanReader.load(self.project)
        except Exception as e:
            logger.warning("Could not load flight plan: %s", e)
            return None

    def save_flight_plan(self) -> None:
        """Save flight plan to project."""
        if self.flight_plan is None:
            return
        try:
            from waypoints.models.flight_plan import FlightPlanWriter

            writer = FlightPlanWriter(self.project)
            writer.save(self.flight_plan)
        except Exception as e:
            logger.error("Failed to save flight plan: %s", e)

    def _log_waypoint_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Log a waypoint change event to the audit trail.

        Args:
            event_type: Type of event (generated, added, deleted, etc.)
            data: Event-specific data payload
        """
        try:
            from waypoints.models.waypoint_history import WaypointHistoryWriter

            writer = WaypointHistoryWriter(self.project)

            # Dispatch to appropriate logging method
            if event_type == "generated":
                writer.log_generated(data.get("waypoints", []))
            elif event_type == "added":
                writer.log_added(
                    data.get("waypoint", {}),
                    data.get("insert_after"),
                )
            elif event_type == "deleted":
                writer.log_deleted(
                    data.get("waypoint_id", ""),
                    data.get("waypoint", {}),
                )
            elif event_type == "updated":
                writer.log_updated(
                    data.get("waypoint_id", ""),
                    data.get("before", {}),
                    data.get("after", {}),
                )
            elif event_type == "broken_down":
                writer.log_broken_down(
                    data.get("parent_id", ""),
                    data.get("sub_waypoints", []),
                )
            elif event_type == "reprioritized":
                writer.log_reprioritized(
                    data.get("previous_order", []),
                    data.get("new_order", []),
                    data.get("rationale", ""),
                    data.get("changes"),
                )
            else:
                logger.warning("Unknown waypoint event type: %s", event_type)
        except Exception as e:
            logger.error("Failed to log waypoint event: %s", e)

    def _load_product_spec(self) -> str:
        """Load product specification from project."""
        spec_path = self.project.get_path() / "docs" / "product-spec.md"
        if spec_path.exists():
            return spec_path.read_text()
        return ""
