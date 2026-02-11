"""Journey coordinator — thin facade delegating to phase-specific classes.

This module orchestrates business logic independent of UI by delegating
to phase-specific classes: FlyPhase (execution), ChartPhase (planning),
and ShapePhase (ideation). Screens call coordinator methods; the
coordinator routes to the appropriate phase delegate.

Benefits:
- Testable without TUI
- Enables headless/CI mode
- Clear separation of concerns
- Single source of truth for journey state
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoints.fly.executor import ExecutionContext, ExecutionResult
from waypoints.fly.intervention import Intervention, InterventionAction
from waypoints.models import (
    DialogueHistory,
    FlightPlan,
    JourneyState,
    JourneyStateManager,
    Project,
    SessionWriter,
    Waypoint,
    WaypointStatus,
)
from waypoints.orchestration.chart_phase import ChartPhase
from waypoints.orchestration.fly_phase import FlyPhase
from waypoints.orchestration.shape_phase import ShapePhase
from waypoints.orchestration.types import (
    ChunkCallback,
    CompletionStatus,
    NextAction,
    ProgressCallback,
)

if TYPE_CHECKING:
    from waypoints.fly.executor import WaypointExecutor
    from waypoints.git.config import GitConfig
    from waypoints.git.service import GitService
    from waypoints.llm.client import ChatClient
    from waypoints.llm.metrics import MetricsCollector

    from .types import (
        BudgetWaitDetails,
        CommitResult,
        InterventionPresentation,
        RollbackResult,
    )

logger = logging.getLogger(__name__)


class JourneyCoordinator:
    """Coordinates journey phases independent of UI.

    This class is a thin facade that delegates to phase-specific classes:
    - FlyPhase: waypoint selection, execution, completion
    - ChartPhase: flight plan generation, waypoint CRUD
    - ShapePhase: Q&A dialogue management, briefs, specs

    Screens call coordinator methods and render results.
    The coordinator manages shared state and persistence.
    """

    def __init__(
        self,
        project: Project,
        flight_plan: FlightPlan | None = None,
        llm: "ChatClient | None" = None,
        git: "GitService | None" = None,
        metrics: "MetricsCollector | None" = None,
    ) -> None:
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

        # Phase delegates
        self._fly = FlyPhase(self)
        self._chart = ChartPhase(self)
        self._shape = ShapePhase(self)

    # ─── Properties ──────────────────────────────────────────────────

    @property
    def flight_plan(self) -> FlightPlan | None:
        """Get the current flight plan, loading if necessary."""
        if self._flight_plan is None:
            self._flight_plan = self._load_flight_plan()
        return self._flight_plan

    @flight_plan.setter
    def flight_plan(self, value: FlightPlan | None) -> None:
        self._flight_plan = value

    @property
    def current_waypoint(self) -> Waypoint | None:
        return self._current_waypoint

    @current_waypoint.setter
    def current_waypoint(self, waypoint: Waypoint | None) -> None:
        self._current_waypoint = waypoint

    @property
    def product_spec(self) -> str:
        """Load and return the product specification."""
        return self._load_product_spec()

    def can_transition(self, target: JourneyState) -> bool:
        return self._state_manager.is_transition_allowed(target)

    def transition(self, target: JourneyState, reason: str | None = None) -> None:
        self._state_manager.transition(target, reason=reason)

    def is_epic(self, waypoint_id: str) -> bool:
        if self.flight_plan is None:
            return False
        return self.flight_plan.is_epic(waypoint_id)

    # ─── Shared State Operations ─────────────────────────────────────

    def reset_stale_in_progress(self) -> bool:
        """Reset any stale IN_PROGRESS waypoints to PENDING."""
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
        waypoint.status = status
        self.save_flight_plan()

    # ─── FLY Phase Delegation ────────────────────────────────────────

    def select_next_waypoint(self, include_failed: bool = False) -> Waypoint | None:
        return self._fly.select_next_waypoint(include_failed)

    def create_executor(
        self,
        waypoint: Waypoint,
        spec: str,
        on_progress: "Callable[[ExecutionContext], None] | None" = None,
        max_iterations: int = 10,
        host_validations_enabled: bool = True,
    ) -> "WaypointExecutor":
        return self._fly.create_executor(
            waypoint, spec, on_progress, max_iterations, host_validations_enabled
        )

    def cancel_execution(self) -> None:
        self._fly.cancel_execution()

    def clear_executor(self) -> None:
        self._fly.clear_executor()

    def log_pause(self) -> None:
        self._fly.log_pause()

    def log_git_commit(self, success: bool, commit_hash: str, message: str) -> None:
        self._fly.log_git_commit(success, commit_hash, message)

    def log_intervention_resolved(self, action: str, **params: Any) -> None:
        self._fly.log_intervention_resolved(action, **params)

    def classify_intervention(
        self, intervention: "Intervention"
    ) -> "InterventionPresentation":
        return self._fly.classify_intervention(intervention)

    def store_worker_intervention(self, intervention: "Intervention") -> None:
        self._fly.store_worker_intervention(intervention)

    def take_worker_intervention(self) -> "Intervention | None":
        return self._fly.take_worker_intervention()

    def clear_intervention(self) -> None:
        self._fly.clear_intervention()

    @property
    def current_intervention(self) -> "Intervention | None":
        return self._fly.current_intervention

    @property
    def active_executor(self) -> "WaypointExecutor | None":
        """Currently active fly executor, if any."""
        return self._fly.active_executor

    def compute_budget_wait(
        self,
        intervention: "Intervention",
        current_waypoint_id: str | None = None,
    ) -> "BudgetWaitDetails":
        return self._fly.compute_budget_wait(intervention, current_waypoint_id)

    async def execute_waypoint(
        self,
        waypoint: Waypoint,
        on_progress: ProgressCallback | None = None,
        max_iterations: int = 10,
        host_validations_enabled: bool = True,
    ) -> ExecutionResult:
        return await self._fly.execute_waypoint(
            waypoint, on_progress, max_iterations, host_validations_enabled
        )

    def handle_execution_result(
        self,
        waypoint: Waypoint,
        result: ExecutionResult,
        git_config: "GitConfig | None" = None,
    ) -> NextAction:
        return self._fly.handle_execution_result(waypoint, result, git_config)

    def commit_waypoint(
        self,
        waypoint: Waypoint,
        git_config: "GitConfig | None" = None,
    ) -> "CommitResult":
        return self._fly.commit_waypoint(waypoint, git_config)

    def rollback_to_ref(self, ref: str | None) -> "RollbackResult":
        return self._fly.rollback_to_ref(ref)

    def rollback_to_tag(self, tag: str | None) -> "RollbackResult":
        """Compatibility wrapper for legacy rollback tag naming."""
        return self.rollback_to_ref(tag)

    def check_parent_completion(self, waypoint: Waypoint) -> None:
        self._fly.check_parent_completion(waypoint)

    def handle_intervention(
        self,
        intervention: Intervention,
        action: InterventionAction,
        additional_iterations: int = 5,
        rollback_ref: str | None = None,
        rollback_tag: str | None = None,
    ) -> NextAction:
        return self._fly.handle_intervention(
            intervention,
            action,
            additional_iterations,
            rollback_ref=rollback_ref,
            rollback_tag=rollback_tag,
        )

    def get_completion_status(self) -> CompletionStatus:
        return self._fly.get_completion_status()

    # ─── CHART Phase Delegation ──────────────────────────────────────

    def generate_flight_plan(
        self, spec: str, on_chunk: ChunkCallback | None = None
    ) -> FlightPlan:
        return self._chart.generate_flight_plan(spec, on_chunk)

    def break_down_waypoint(
        self, waypoint: Waypoint, on_chunk: ChunkCallback | None = None
    ) -> list[Waypoint]:
        return self._chart.break_down_waypoint(waypoint, on_chunk)

    def generate_waypoint(
        self,
        description: str,
        spec_summary: str | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> tuple[Waypoint, str | None]:
        return self._chart.generate_waypoint(description, spec_summary, on_chunk)

    def suggest_reprioritization(
        self,
        spec_summary: str | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> tuple[list[str], str, list[dict[str, str]]]:
        return self._chart.suggest_reprioritization(spec_summary, on_chunk)

    def update_waypoint(self, waypoint: Waypoint) -> None:
        self._chart.update_waypoint(waypoint)

    def delete_waypoint(self, waypoint_id: str) -> list[str]:
        return self._chart.delete_waypoint(waypoint_id)

    def add_sub_waypoints(self, parent_id: str, sub_waypoints: list[Waypoint]) -> None:
        self._chart.add_sub_waypoints(parent_id, sub_waypoints)

    def add_waypoint(self, waypoint: Waypoint, after_id: str | None = None) -> None:
        self._chart.add_waypoint(waypoint, after_id)

    def fork_debug_waypoint(self, waypoint: Waypoint, note: str) -> Waypoint:
        return self._chart.fork_debug_waypoint(waypoint, note)

    def reorder_waypoints(
        self,
        new_order: list[str],
        rationale: str = "",
        changes: list[dict[str, str]] | None = None,
    ) -> None:
        self._chart.reorder_waypoints(new_order, rationale, changes)

    # ─── SHAPE Phase Delegation ──────────────────────────────────────

    @property
    def dialogue_history(self) -> DialogueHistory | None:
        return self._shape.dialogue_history

    def start_qa_dialogue(
        self, idea: str, on_chunk: ChunkCallback | None = None
    ) -> str:
        return self._shape.start_qa_dialogue(idea, on_chunk)

    def continue_qa_dialogue(
        self, user_response: str, on_chunk: ChunkCallback | None = None
    ) -> str:
        return self._shape.continue_qa_dialogue(user_response, on_chunk)

    def resume_qa_dialogue(self, history: DialogueHistory, session_file: Path) -> None:
        self._shape.resume_qa_dialogue(history, session_file)

    def generate_idea_brief(
        self,
        history: DialogueHistory | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        return self._shape.generate_idea_brief(history, on_chunk)

    def generate_product_spec(
        self, brief: str, on_chunk: ChunkCallback | None = None
    ) -> str:
        return self._shape.generate_product_spec(brief, on_chunk)

    # ─── Persistence & Logging ───────────────────────────────────────

    def _load_flight_plan(self) -> FlightPlan | None:
        try:
            from waypoints.models.flight_plan import FlightPlanReader

            return FlightPlanReader.load(self.project)
        except Exception as e:
            logger.warning("Could not load flight plan: %s", e)
            return None

    def save_flight_plan(self) -> None:
        if self.flight_plan is None:
            return
        try:
            from waypoints.models.flight_plan import FlightPlanWriter

            writer = FlightPlanWriter(self.project)
            writer.save(self.flight_plan)
        except Exception as e:
            logger.error("Failed to save flight plan: %s", e)

    def log_waypoint_event(self, event_type: str, data: dict[str, Any]) -> None:
        try:
            from waypoints.models.waypoint_history import WaypointHistoryWriter

            writer = WaypointHistoryWriter(self.project)

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
            elif event_type == "debug_forked":
                pass  # Logged inline by chart_phase
            else:
                logger.warning("Unknown waypoint event type: %s", event_type)
        except Exception as e:
            logger.error("Failed to log waypoint event: %s", e)

    def _load_product_spec(self) -> str:
        spec_path = self.project.get_path() / "docs" / "product-spec.md"
        if spec_path.exists():
            return spec_path.read_text()
        return ""
