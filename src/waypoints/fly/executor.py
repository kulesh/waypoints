"""Waypoint executor using Ralph-style iterative agentic execution.

This module implements the core execution loop where Claude autonomously
implements waypoints by writing code, running tests, and iterating until
completion or max iterations reached.

Key patterns from Ralph Wiggum technique:
- Iterative loop until completion marker detected
- File system as context (Claude reads its own previous work)
- Clear completion markers: <waypoint-complete>WP-XXX</waypoint-complete>
- Git checkpoints for progress

Model-centric architecture ("Pilot and Dog"):
- Model runs conceptual checklist, produces receipt
- Code validates receipt before allowing commit
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from waypoints.config.app_root import dangerous_app_root
from waypoints.config.settings import settings
from waypoints.fly.evidence import (
    CRITERION_PATTERN,
    VALIDATION_PATTERN,
    FileOperation,
)
from waypoints.fly.evidence import (
    detect_validation_category as _detect_validation_category,
)
from waypoints.fly.evidence import (
    extract_file_operation as _extract_file_operation,
)
from waypoints.fly.evidence import (
    normalize_command as _normalize_command,
)
from waypoints.fly.evidence import (
    parse_tool_output as _parse_tool_output,
)
from waypoints.fly.execution_log import ExecutionLogWriter
from waypoints.fly.intervention import (
    Intervention,
    InterventionNeededError,
    InterventionType,
)
from waypoints.fly.protocol import parse_stage_reports
from waypoints.fly.provenance import (
    WorkspaceDiffSummary,
    WorkspaceSnapshot,
    capture_workspace_snapshot,
    summarize_workspace_diff,
)
from waypoints.fly.stack import ValidationCommand
from waypoints.git.config import Checklist
from waypoints.git.receipt import (
    CapturedEvidence,
    CriterionVerification,
)
from waypoints.llm.client import (
    APIErrorType,
    StreamChunk,
    StreamComplete,
    StreamToolUse,
    agent_query,
    classify_api_error,
    extract_reset_datetime,
    extract_reset_time,
)
from waypoints.llm.metrics import BudgetExceededError
from waypoints.llm.prompts import build_execution_prompt
from waypoints.llm.providers.base import (
    BUDGET_PATTERNS,
    RATE_LIMIT_PATTERNS,
    UNAVAILABLE_PATTERNS,
)
from waypoints.memory import (
    ProjectMemoryIndex,
    WaypointMemoryRecord,
    build_waypoint_memory_context,
    format_directory_policy_for_prompt,
    load_or_build_project_memory,
    save_waypoint_memory,
)
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint

if TYPE_CHECKING:
    from waypoints.fly.receipt_finalizer import ReceiptFinalizer
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)

# Max iterations before giving up
MAX_ITERATIONS = 10
MAX_PROTOCOL_DERAILMENT_STREAK = 2
COMPLETION_ALIAS_HINTS = (
    "waypoint_complete",
    "waypoint completed",
    "==completed==",
    "implementation is complete",
    "all waypoints",
)


class ExecutionResult(Enum):
    """Result of waypoint execution."""

    SUCCESS = "success"
    FAILED = "failed"
    MAX_ITERATIONS = "max_iterations"
    CANCELLED = "cancelled"
    INTERVENTION_NEEDED = "intervention_needed"


@dataclass
class ExecutionStep:
    """A single step in waypoint execution."""

    iteration: int
    action: str
    output: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ExecutionContext:
    """Context passed to callbacks during execution."""

    waypoint: Waypoint
    iteration: int
    total_iterations: int
    step: str
    output: str
    criteria_completed: set[int] = field(default_factory=set)
    file_operations: list[FileOperation] = field(default_factory=list)


ProgressCallback = Callable[[ExecutionContext], None]


class WaypointExecutor:
    """Executes a waypoint using agentic AI.

    Uses the Ralph-style iterative loop pattern where Claude has full
    access to file and bash tools to implement code autonomously.
    """

    def __init__(
        self,
        project: Project,
        waypoint: Waypoint,
        spec: str,
        on_progress: ProgressCallback | None = None,
        max_iterations: int = MAX_ITERATIONS,
        metrics_collector: "MetricsCollector | None" = None,
        host_validations_enabled: bool = True,
    ) -> None:
        self.project = project
        self.waypoint = waypoint
        self.spec = spec
        self.on_progress = on_progress
        self.max_iterations = max_iterations
        self.metrics_collector = metrics_collector
        self.host_validations_enabled = host_validations_enabled
        self.steps: list[ExecutionStep] = []
        self._cancelled = False
        self._log_writer: ExecutionLogWriter | None = None
        self._validation_commands: list[ValidationCommand] = []
        self._file_operations: list[FileOperation] = []
        self._project_memory_index: ProjectMemoryIndex | None = None
        self._directory_policy_context: str | None = None
        self._waypoint_memory_context: str | None = None

    def cancel(self) -> None:
        """Cancel the execution."""
        self._cancelled = True

    async def execute(self) -> ExecutionResult:
        """Execute the waypoint using iterative agentic loop.

        Returns the execution result (success, failed, max_iterations, etc.)
        """
        project_path = self.project.get_path()
        app_root = dangerous_app_root()
        if project_path.resolve().is_relative_to(app_root):
            raise RuntimeError(
                "Project directory resolves inside the Waypoints app directory; "
                "refusing to execute."
            )

        # Defense in depth: ensure we're in the project directory
        original_cwd = os.getcwd()
        os.chdir(project_path)
        try:
            result = await self._execute_impl(project_path)

            # Post-execution validation: check for escapes
            violations = self._validate_no_external_changes(project_path)
            if violations:
                logger.error(
                    "SECURITY: Agent escaped project directory! Violations:\n%s",
                    "\n".join(violations),
                )
                # Log violation to execution log
                if self._log_writer:
                    files = ", ".join(violations[:5])
                    details = f"{len(violations)} external file(s): {files}"
                    self._log_writer.log_security_violation(
                        self.max_iterations, details
                    )
                # Log violation but don't fail - the damage is done
                # This is a defense-in-depth warning for investigation
                self._report_progress(
                    self.max_iterations,
                    self.max_iterations,
                    "warning",
                    f"Security warning: {len(violations)} external file(s) modified",
                )

            return result
        finally:
            os.chdir(original_cwd)

    async def _execute_impl(self, project_path: Path) -> ExecutionResult:
        """Internal implementation of execute, runs in project directory."""
        # Initialize execution log early — _make_finalizer requires it
        self._log_writer = ExecutionLogWriter(self.project, self.waypoint)

        # Load checklist from project (creates default if not exists)
        checklist = Checklist.load(self.project)
        self._validation_commands = self._resolve_validation_commands(
            project_path, checklist
        )
        self._refresh_project_memory(project_path)

        prompt = build_execution_prompt(
            self.waypoint,
            self.spec,
            project_path,
            checklist,
            directory_policy_context=self._directory_policy_context,
            waypoint_memory_context=self._waypoint_memory_context,
        )

        logger.info(
            "Starting execution of %s: %s", self.waypoint.id, self.waypoint.title
        )
        logger.info("Execution log: %s", self._log_writer.file_path)
        workspace_before = self._capture_workspace_snapshot(project_path)

        iteration = 0
        full_output = ""
        reported_validation_commands: list[str] = []
        # Criteria verification evidence
        captured_criteria: dict[int, CriterionVerification] = {}
        tool_validation_evidence: dict[str, CapturedEvidence] = {}
        tool_validation_categories: dict[str, CapturedEvidence] = {}
        logged_stage_reports: set[tuple[object, ...]] = set()
        completion_marker = f"<waypoint-complete>{self.waypoint.id}</waypoint-complete>"
        completion_detected = False
        completion_iteration: int | None = None
        completion_output: str | None = None
        completion_criteria: set[int] | None = None
        resume_session_id: str | None = None
        next_reason_code = "initial"
        next_reason_detail = "Initial waypoint execution."
        protocol_derailment_streak = 0
        protocol_derailments: list[str] = []

        while iteration < self.max_iterations:
            if self._cancelled:
                logger.info("Execution cancelled")
                self._log_writer.log_completion(ExecutionResult.CANCELLED.value)
                workspace_summary = self._log_workspace_provenance(
                    project_path,
                    workspace_before,
                    iteration,
                    ExecutionResult.CANCELLED.value,
                )
                self._persist_waypoint_memory(
                    project_path=project_path,
                    result=ExecutionResult.CANCELLED.value,
                    iteration=iteration,
                    reported_validation_commands=reported_validation_commands,
                    captured_criteria=captured_criteria,
                    tool_validation_evidence=tool_validation_evidence,
                    protocol_derailments=protocol_derailments,
                    workspace_summary=workspace_summary,
                )
                return ExecutionResult.CANCELLED

            iteration += 1
            logger.info("Iteration %d/%d", iteration, self.max_iterations)

            self._report_progress(
                iteration, self.max_iterations, "executing", f"Iteration {iteration}"
            )

            # Log iteration start
            reason_code = next_reason_code
            reason_detail = next_reason_detail
            iter_prompt = (
                prompt
                if iteration == 1
                else self._build_iteration_kickoff_prompt(
                    reason_code=reason_code,
                    reason_detail=reason_detail,
                    completion_marker=completion_marker,
                    captured_criteria=captured_criteria,
                )
            )
            self._log_writer.log_iteration_start(
                iteration=iteration,
                prompt=iter_prompt,
                reason_code=reason_code,
                reason_detail=reason_detail,
                resume_session_id=resume_session_id,
            )

            # Run agent query with file and bash tools
            iteration_output = ""
            iteration_cost: float | None = None
            iteration_file_ops: list[FileOperation] = []
            iteration_stage_reports_logged = 0
            scope_drift_detected = False
            try:
                async for chunk in agent_query(
                    prompt=iter_prompt,
                    system_prompt=self._get_system_prompt(),
                    allowed_tools=[
                        "Read",
                        "Write",
                        "Edit",
                        "Bash",
                        "Glob",
                        "Grep",
                        "WebSearch",
                        "WebFetch",
                    ],
                    cwd=str(project_path),
                    resume_session_id=resume_session_id,
                    metrics_collector=self.metrics_collector,
                    phase="fly",
                    waypoint_id=self.waypoint.id,
                ):
                    if isinstance(chunk, StreamChunk):
                        iteration_output += chunk.text
                        full_output += chunk.text

                        if not completion_detected:
                            # Parse validation evidence markers BEFORE completion check
                            for match in VALIDATION_PATTERN.findall(full_output):
                                command, _, _ = match
                                normalized_command = command.strip()
                                if not normalized_command:
                                    continue
                                if normalized_command in reported_validation_commands:
                                    continue

                                reported_validation_commands.append(normalized_command)
                                category = _detect_validation_category(
                                    normalized_command
                                )
                                if category:
                                    logger.info(
                                        "Model reported validation command for %s: %s",
                                        category,
                                        normalized_command,
                                    )

                            # Parse criterion verification markers
                            for match in CRITERION_PATTERN.findall(full_output):
                                index, status, text, evidence = match
                                idx = int(index)
                                if idx not in captured_criteria:
                                    captured_criteria[idx] = CriterionVerification(
                                        index=idx,
                                        criterion=text.strip(),
                                        status=status,
                                        evidence=evidence.strip(),
                                        verified_at=datetime.now(UTC),
                                    )
                                    logger.info(
                                        "Captured criterion verification: [%d] %s",
                                        idx,
                                        status,
                                    )

                            # Parse structured execution stage reports
                            for report in parse_stage_reports(full_output):
                                key = (
                                    report.stage,
                                    report.success,
                                    report.output,
                                    tuple(report.artifacts),
                                    report.next_stage,
                                )
                                if key in logged_stage_reports:
                                    continue
                                logged_stage_reports.add(key)
                                iteration_stage_reports_logged += 1
                                self._log_writer.log_stage_report(iteration, report)
                                output = report.output.strip()
                                if len(output) > 400:
                                    output = output[:400] + "..."
                                summary = f"{report.stage.value}: {output}".strip()
                                self._report_progress(
                                    iteration,
                                    self.max_iterations,
                                    "stage",
                                    summary,
                                )

                            # Check for completion marker
                            if completion_marker in full_output:
                                logger.info("Completion marker found!")
                                completion_detected = True
                                completion_iteration = iteration
                                completion_output = iteration_output
                                criterion_matches = CRITERION_PATTERN.findall(
                                    full_output
                                )
                                completion_criteria = {
                                    int(m[0]) for m in criterion_matches
                                }
                                self._log_writer.log_completion_detected(iteration)

                        if completion_detected:
                            continue

                        # Parse criterion completion markers from full output
                        criterion_matches = CRITERION_PATTERN.findall(full_output)
                        completed_indices = {int(m[0]) for m in criterion_matches}

                        # Report streaming progress with criteria status
                        self._report_progress(
                            iteration,
                            self.max_iterations,
                            "streaming",
                            chunk.text,
                            criteria_completed=completed_indices,
                        )

                    elif isinstance(chunk, StreamToolUse):
                        # Log tool call (input only, output handled by SDK)
                        self._log_writer.log_tool_call(
                            iteration,
                            chunk.tool_name,
                            chunk.tool_input,
                            chunk.tool_output,
                        )
                        if (
                            isinstance(chunk.tool_output, str)
                            and "Error: Access denied:" in chunk.tool_output
                        ):
                            scope_drift_detected = True
                        if chunk.tool_name == "Bash":
                            command = chunk.tool_input.get("command")
                            if isinstance(command, str) and chunk.tool_output:
                                category = _detect_validation_category(command)
                                stdout, stderr, exit_code = _parse_tool_output(
                                    chunk.tool_output
                                )
                                evidence = CapturedEvidence(
                                    command=command,
                                    exit_code=exit_code,
                                    stdout=stdout,
                                    stderr=stderr,
                                    captured_at=datetime.now(UTC),
                                )
                                tool_validation_evidence[
                                    _normalize_command(command)
                                ] = evidence
                                if category:
                                    tool_validation_categories[category] = evidence
                        # Extract and track file operation
                        file_op = _extract_file_operation(
                            chunk.tool_name, chunk.tool_input
                        )
                        if file_op:
                            iteration_file_ops.append(file_op)
                            self._file_operations.append(file_op)
                            # Report progress with updated file operations
                            self._report_progress(
                                iteration,
                                self.max_iterations,
                                "tool_use",
                                f"{file_op.tool_name}: {file_op.file_path}",
                                file_operations=iteration_file_ops,
                            )

                    elif isinstance(chunk, StreamComplete):
                        iteration_cost = chunk.cost_usd
                        if chunk.session_id:
                            resume_session_id = chunk.session_id
                        logger.info(
                            "Iteration %d complete, cost: $%.4f",
                            iteration,
                            chunk.cost_usd or 0,
                        )

                if completion_detected:
                    self.steps.append(
                        ExecutionStep(
                            iteration=completion_iteration or iteration,
                            action="complete",
                            output=completion_output or iteration_output,
                        )
                    )
                    final_completed = completion_criteria or set()
                    self._log_writer.log_output(
                        iteration,
                        completion_output or iteration_output,
                        final_completed,
                    )
                    self._log_writer.log_iteration_end(iteration, iteration_cost)

                    finalizer = self._make_finalizer()
                    receipt_valid = await finalizer.finalize(
                        project_path=project_path,
                        captured_criteria=captured_criteria,
                        validation_commands=self._validation_commands,
                        reported_validation_commands=reported_validation_commands,
                        tool_validation_evidence=tool_validation_evidence,
                        tool_validation_categories=tool_validation_categories,
                        host_validations_enabled=self.host_validations_enabled,
                        max_iterations=self.max_iterations,
                    )

                    if receipt_valid:
                        self._report_progress(
                            iteration,
                            MAX_ITERATIONS,
                            "complete",
                            "Waypoint complete with valid receipt!",
                        )
                        self._log_writer.log_completion(ExecutionResult.SUCCESS.value)
                        workspace_summary = self._log_workspace_provenance(
                            project_path,
                            workspace_before,
                            iteration,
                            ExecutionResult.SUCCESS.value,
                        )
                        self._persist_waypoint_memory(
                            project_path=project_path,
                            result=ExecutionResult.SUCCESS.value,
                            iteration=iteration,
                            reported_validation_commands=reported_validation_commands,
                            captured_criteria=captured_criteria,
                            tool_validation_evidence=tool_validation_evidence,
                            protocol_derailments=protocol_derailments,
                            workspace_summary=workspace_summary,
                        )
                        return ExecutionResult.SUCCESS

                    logger.warning(
                        "Waypoint marked complete but receipt invalid. "
                        "Git commit will be skipped."
                    )
                    self._report_progress(
                        iteration,
                        MAX_ITERATIONS,
                        "complete",
                        "Complete (receipt missing/invalid)",
                    )
                    self._log_writer.log_completion(ExecutionResult.SUCCESS.value)
                    workspace_summary = self._log_workspace_provenance(
                        project_path,
                        workspace_before,
                        iteration,
                        ExecutionResult.SUCCESS.value,
                    )
                    self._persist_waypoint_memory(
                        project_path=project_path,
                        result=ExecutionResult.SUCCESS.value,
                        iteration=iteration,
                        reported_validation_commands=reported_validation_commands,
                        captured_criteria=captured_criteria,
                        tool_validation_evidence=tool_validation_evidence,
                        protocol_derailments=protocol_derailments,
                        workspace_summary=workspace_summary,
                        error_summary="Receipt invalid or missing despite completion.",
                    )
                    return ExecutionResult.SUCCESS

            except Exception as e:
                logger.exception("Error during iteration %d: %s", iteration, e)

                # Classify the error for better user feedback
                api_error_type = classify_api_error(e)
                # If output mentions budget/rate-limit issues, override classification
                lower_output = full_output.lower()
                if api_error_type == APIErrorType.UNKNOWN:
                    for pattern in BUDGET_PATTERNS:
                        if pattern in lower_output:
                            api_error_type = APIErrorType.BUDGET_EXCEEDED
                            break
                    if api_error_type == APIErrorType.UNKNOWN:
                        for pattern in RATE_LIMIT_PATTERNS:
                            if pattern in lower_output:
                                api_error_type = APIErrorType.RATE_LIMITED
                                break
                        if api_error_type == APIErrorType.UNKNOWN:
                            for pattern in UNAVAILABLE_PATTERNS:
                                if pattern in lower_output:
                                    api_error_type = APIErrorType.API_UNAVAILABLE
                                    break

                # Map API error type to intervention type
                intervention_type = {
                    APIErrorType.RATE_LIMITED: InterventionType.RATE_LIMITED,
                    APIErrorType.API_UNAVAILABLE: InterventionType.API_UNAVAILABLE,
                    APIErrorType.BUDGET_EXCEEDED: InterventionType.BUDGET_EXCEEDED,
                }.get(api_error_type, InterventionType.EXECUTION_ERROR)

                # Create user-friendly error summary
                error_summaries = {
                    APIErrorType.RATE_LIMITED: (
                        "Model provider rate limit reached. "
                        "Wait a few minutes and retry."
                    ),
                    APIErrorType.API_UNAVAILABLE: (
                        "Model provider temporarily unavailable. Try again shortly."
                    ),
                    APIErrorType.BUDGET_EXCEEDED: (
                        "Model usage budget exceeded. "
                        "Execution paused until budget resets."
                    ),
                }
                error_summary = error_summaries.get(api_error_type, str(e))

                # For budget errors, include additional budget context when possible.
                # Some providers wrap errors in generic subprocess failures while the
                # streamed text still includes reset timing (e.g. "resets 1am (...)").
                reset_at = extract_reset_datetime(str(e))
                if reset_at is None and full_output:
                    reset_at = extract_reset_datetime(full_output)
                if api_error_type == APIErrorType.BUDGET_EXCEEDED:
                    if isinstance(e, BudgetExceededError):
                        error_summary = (
                            f"Configured budget ${e.limit_value:.2f} reached "
                            f"(current ${e.current_value:.2f}). "
                            "Execution paused until you increase the budget."
                        )
                    elif (reset_time := extract_reset_time(str(e))) is not None:
                        error_summary = (
                            f"Model usage budget exceeded. Resets {reset_time}."
                        )
                    elif (reset_time := extract_reset_time(full_output)) is not None:
                        error_summary = (
                            f"Model usage budget exceeded. Resets {reset_time}."
                        )

                self._report_progress(
                    iteration, self.max_iterations, "error", f"Error: {error_summary}"
                )
                self.steps.append(
                    ExecutionStep(
                        iteration=iteration,
                        action="error",
                        output=str(e),
                    )
                )
                # Log error
                self._log_writer.log_error(iteration, str(e))
                self._log_writer.log_completion(ExecutionResult.FAILED.value)

                # Raise InterventionNeededError with classified type
                intervention = Intervention(
                    type=intervention_type,
                    waypoint=self.waypoint,
                    iteration=iteration,
                    max_iterations=self.max_iterations,
                    error_summary=error_summary,
                    context={
                        "full_output": full_output[-2000:],
                        "api_error_type": api_error_type.value,
                        "original_error": str(e),
                        "configured_budget_usd": settings.llm_budget_usd,
                        "current_cost_usd": (
                            self.metrics_collector.total_cost
                            if self.metrics_collector
                            else None
                        ),
                        "resume_at_utc": (
                            reset_at.astimezone(UTC).isoformat() if reset_at else None
                        ),
                    },
                )
                self._log_writer.log_intervention_needed(
                    iteration, intervention.type.value, error_summary
                )
                workspace_summary = self._log_workspace_provenance(
                    project_path,
                    workspace_before,
                    iteration,
                    ExecutionResult.INTERVENTION_NEEDED.value,
                )
                self._persist_waypoint_memory(
                    project_path=project_path,
                    result=ExecutionResult.INTERVENTION_NEEDED.value,
                    iteration=iteration,
                    reported_validation_commands=reported_validation_commands,
                    captured_criteria=captured_criteria,
                    tool_validation_evidence=tool_validation_evidence,
                    protocol_derailments=protocol_derailments,
                    workspace_summary=workspace_summary,
                    error_summary=error_summary,
                )
                raise InterventionNeededError(intervention) from e

            # Parse final criteria state and log iteration output
            final_criteria = CRITERION_PATTERN.findall(full_output)
            final_completed = {int(m[0]) for m in final_criteria}
            self._log_writer.log_output(iteration, iteration_output, final_completed)
            self._log_writer.log_iteration_end(iteration, iteration_cost)
            verified_criteria = {
                idx
                for idx, criterion in captured_criteria.items()
                if criterion.status == "verified"
            }
            unresolved_criteria = sorted(
                set(range(len(self.waypoint.acceptance_criteria))) - verified_criteria
            )
            protocol_issues = self._detect_protocol_issues(
                iteration_output=iteration_output,
                completion_marker=completion_marker,
                stage_reports_logged=iteration_stage_reports_logged,
                scope_drift_detected=scope_drift_detected,
            )
            if protocol_issues:
                escalation_issues = [
                    issue
                    for issue in protocol_issues
                    if issue != "missing structured stage report"
                ]
                protocol_derailments.extend(protocol_issues)
                if escalation_issues:
                    protocol_derailment_streak += 1
                else:
                    protocol_derailment_streak = 0
                next_reason_code = "protocol_violation"
                next_reason_detail = "; ".join(protocol_issues)
                self._log_writer.log_protocol_derailment(
                    iteration=iteration,
                    issues=protocol_issues,
                    action=(
                        "escalate_intervention"
                        if protocol_derailment_streak >= MAX_PROTOCOL_DERAILMENT_STREAK
                        else "nudge_and_retry"
                    ),
                )
            elif unresolved_criteria:
                protocol_derailment_streak = 0
                remaining_labels = ", ".join(
                    f"[{idx}] {self.waypoint.acceptance_criteria[idx]}"
                    for idx in unresolved_criteria
                )
                next_reason_code = "incomplete_criteria"
                next_reason_detail = (
                    f"{len(unresolved_criteria)} criteria unresolved: "
                    f"{remaining_labels}"
                )
            else:
                protocol_derailment_streak = 0
                next_reason_code = "validation_failure"
                next_reason_detail = (
                    "Criteria appear complete, but completion protocol was "
                    "not satisfied."
                )

            if protocol_derailment_streak >= MAX_PROTOCOL_DERAILMENT_STREAK:
                summary = (
                    "Execution repeatedly violated waypoint protocol. "
                    f"Issues: {next_reason_detail}"
                )
                self._log_writer.log_error(iteration, summary)
                self._log_writer.log_completion(
                    ExecutionResult.INTERVENTION_NEEDED.value
                )
                intervention = Intervention(
                    type=InterventionType.EXECUTION_ERROR,
                    waypoint=self.waypoint,
                    iteration=iteration,
                    max_iterations=self.max_iterations,
                    error_summary=summary,
                    context={
                        "full_output": full_output[-2000:],
                        "reason_code": next_reason_code,
                    },
                )
                self._log_writer.log_intervention_needed(
                    iteration, intervention.type.value, summary
                )
                workspace_summary = self._log_workspace_provenance(
                    project_path,
                    workspace_before,
                    iteration,
                    ExecutionResult.INTERVENTION_NEEDED.value,
                )
                self._persist_waypoint_memory(
                    project_path=project_path,
                    result=ExecutionResult.INTERVENTION_NEEDED.value,
                    iteration=iteration,
                    reported_validation_commands=reported_validation_commands,
                    captured_criteria=captured_criteria,
                    tool_validation_evidence=tool_validation_evidence,
                    protocol_derailments=protocol_derailments,
                    workspace_summary=workspace_summary,
                    error_summary=summary,
                )
                raise InterventionNeededError(intervention)

            # Record step
            self.steps.append(
                ExecutionStep(
                    iteration=iteration,
                    action="iterate",
                    output=iteration_output,
                )
            )

            # Check if agent is stuck or needs human help
            if self._needs_intervention(iteration_output):
                logger.info("Human intervention needed")
                reason = self._extract_intervention_reason(iteration_output)
                self._log_writer.log_error(iteration, f"Intervention needed: {reason}")
                self._log_writer.log_completion(
                    ExecutionResult.INTERVENTION_NEEDED.value
                )
                intervention = Intervention(
                    type=InterventionType.USER_REQUESTED,
                    waypoint=self.waypoint,
                    iteration=iteration,
                    max_iterations=self.max_iterations,
                    error_summary=reason,
                    context={"full_output": full_output[-2000:]},
                )
                self._log_writer.log_intervention_needed(
                    iteration, intervention.type.value, reason
                )
                workspace_summary = self._log_workspace_provenance(
                    project_path,
                    workspace_before,
                    iteration,
                    ExecutionResult.INTERVENTION_NEEDED.value,
                )
                self._persist_waypoint_memory(
                    project_path=project_path,
                    result=ExecutionResult.INTERVENTION_NEEDED.value,
                    iteration=iteration,
                    reported_validation_commands=reported_validation_commands,
                    captured_criteria=captured_criteria,
                    tool_validation_evidence=tool_validation_evidence,
                    protocol_derailments=protocol_derailments,
                    workspace_summary=workspace_summary,
                    error_summary=reason,
                )
                raise InterventionNeededError(intervention)

        # Max iterations reached
        logger.warning(
            "Max iterations (%d) reached without completion", self.max_iterations
        )
        error_msg = (
            f"Waypoint did not complete after {self.max_iterations} iterations. "
            "The agent may be stuck or the task may be too complex."
        )
        self._log_writer.log_error(iteration, error_msg)
        self._log_writer.log_completion(ExecutionResult.MAX_ITERATIONS.value)

        intervention = Intervention(
            type=InterventionType.ITERATION_LIMIT,
            waypoint=self.waypoint,
            iteration=iteration,
            max_iterations=self.max_iterations,
            error_summary=error_msg,
            context={"full_output": full_output[-2000:]},
        )
        self._log_writer.log_intervention_needed(
            iteration, intervention.type.value, error_msg
        )
        workspace_summary = self._log_workspace_provenance(
            project_path,
            workspace_before,
            iteration,
            ExecutionResult.INTERVENTION_NEEDED.value,
        )
        self._persist_waypoint_memory(
            project_path=project_path,
            result=ExecutionResult.INTERVENTION_NEEDED.value,
            iteration=iteration,
            reported_validation_commands=reported_validation_commands,
            captured_criteria=captured_criteria,
            tool_validation_evidence=tool_validation_evidence,
            protocol_derailments=protocol_derailments,
            workspace_summary=workspace_summary,
            error_summary=error_msg,
        )
        raise InterventionNeededError(intervention)

    def _build_iteration_kickoff_prompt(
        self,
        reason_code: str,
        reason_detail: str,
        completion_marker: str,
        captured_criteria: dict[int, CriterionVerification],
    ) -> str:
        """Build a focused kickoff prompt for follow-up iterations."""
        verified_criteria = {
            idx
            for idx, criterion in captured_criteria.items()
            if criterion.status == "verified"
        }
        unresolved_lines = [
            f"- [ ] [{idx}] {text}"
            for idx, text in enumerate(self.waypoint.acceptance_criteria)
            if idx not in verified_criteria
        ]
        unresolved_block = (
            "\n".join(unresolved_lines)
            if unresolved_lines
            else "- All criteria appear verified; finish protocol and validation."
        )
        return (
            "Iteration kickoff\n"
            f"Reason: {reason_code}\n"
            f"Details: {reason_detail}\n\n"
            f"Current waypoint: {self.waypoint.id} - {self.waypoint.title}\n"
            f"Objective: {self.waypoint.objective}\n\n"
            "Unresolved criteria:\n"
            f"{unresolved_block}\n\n"
            "Required next action:\n"
            "- Continue implementation from current filesystem state.\n"
            "- Run only necessary validations for changed code.\n"
            "- Report structured execution stages.\n\n"
            "Completion rule (strict):\n"
            f"- Emit exactly: {completion_marker}\n"
            "- Do not use aliases like WAYPOINT_COMPLETE.\n"
            "- Do not re-audit unrelated waypoints."
        )

    def _detect_protocol_issues(
        self,
        iteration_output: str,
        completion_marker: str,
        stage_reports_logged: int,
        scope_drift_detected: bool,
    ) -> list[str]:
        """Detect recoverable protocol issues for iteration-to-iteration nudging."""
        issues: list[str] = []
        lower_output = iteration_output.lower()
        waypoint_alias = f"{self.waypoint.id.lower()} complete"
        claimed_complete = "complete" in lower_output and (
            any(hint in lower_output for hint in COMPLETION_ALIAS_HINTS)
            or waypoint_alias in lower_output
        )
        if claimed_complete and completion_marker not in iteration_output:
            issues.append("claimed completion without exact completion marker")
        if stage_reports_logged == 0:
            issues.append("missing structured stage report")
        if scope_drift_detected:
            issues.append("attempted tool access to blocked project areas")
        return issues

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the agent."""
        project_path = self.project.get_path()
        policy_context = ""
        if self._directory_policy_context:
            policy_context = (
                "\nProject memory policy (generated from repository scan):\n"
                f"{self._directory_policy_context}\n"
            )
        return f"""You are implementing a software waypoint as part of a larger project.
You have access to file and bash tools to read, write, and execute code.

**CRITICAL CONSTRAINTS:**
- Your working directory is: {project_path}
- ONLY access files within this directory
- NEVER use absolute paths outside the project
- NEVER use ../ to escape the project directory
{policy_context}

Work methodically:
1. First understand the existing codebase
2. Make minimal, focused changes
3. Test after each change
4. Iterate until done

When complete, output the completion marker specified in the instructions."""

    def _refresh_project_memory(self, project_path: Path) -> None:
        """Load and cache project memory policy for this execution."""
        try:
            memory = load_or_build_project_memory(project_path)
            self._project_memory_index = memory.index
            self._directory_policy_context = format_directory_policy_for_prompt(
                memory.index
            )
        except Exception:
            logger.exception(
                "Failed to build project memory index for %s", self.waypoint.id
            )
            self._project_memory_index = None
            self._directory_policy_context = None
        try:
            context = build_waypoint_memory_context(
                project_root=project_path,
                waypoint=self.waypoint,
            )
            self._waypoint_memory_context = context or None
        except Exception:
            logger.exception(
                "Failed to build waypoint memory context for %s",
                self.waypoint.id,
            )
            self._waypoint_memory_context = None

    def _report_progress(
        self,
        iteration: int,
        total: int,
        step: str,
        output: str,
        criteria_completed: set[int] | None = None,
        file_operations: list[FileOperation] | None = None,
    ) -> None:
        """Report progress to callback if set."""
        if self.on_progress:
            ctx = ExecutionContext(
                waypoint=self.waypoint,
                iteration=iteration,
                total_iterations=total,
                step=step,
                output=output,
                criteria_completed=criteria_completed or set(),
                file_operations=file_operations or [],
            )
            self.on_progress(ctx)

    def _needs_intervention(self, output: str) -> bool:
        """Check if the output indicates human intervention is needed."""
        intervention_markers = [
            "cannot proceed",
            "need human help",
            "blocked by",
            "unable to complete",
            "requires manual",
        ]
        lower_output = output.lower()
        return any(marker in lower_output for marker in intervention_markers)

    def _extract_intervention_reason(self, output: str) -> str:
        """Extract a meaningful intervention reason from the output."""
        intervention_markers = [
            "cannot proceed",
            "need human help",
            "blocked by",
            "unable to complete",
            "requires manual",
        ]
        lower_output = output.lower()

        # Find which marker was triggered
        for marker in intervention_markers:
            if marker in lower_output:
                # Extract surrounding context
                idx = lower_output.find(marker)
                start = max(0, idx - 100)
                end = min(len(output), idx + len(marker) + 200)
                context = output[start:end].strip()
                return f"Agent indicated: ...{context}..."

        return "Agent requested human intervention"

    def _resolve_validation_commands(
        self, project_path: Path, checklist: Checklist
    ) -> list[ValidationCommand]:
        """Resolve validation commands to run for receipt evidence."""
        finalizer = self._make_finalizer()
        return finalizer.resolve_validation_commands(project_path, checklist, self.spec)

    def _make_finalizer(self) -> "ReceiptFinalizer":
        """Create a ReceiptFinalizer for this executor."""
        from waypoints.fly.receipt_finalizer import ReceiptFinalizer

        assert self._log_writer is not None
        return ReceiptFinalizer(
            project=self.project,
            waypoint=self.waypoint,
            log_writer=self._log_writer,
            metrics_collector=self.metrics_collector,
            progress_callback=self._report_progress,
        )

    def _capture_workspace_snapshot(
        self, project_path: Path
    ) -> WorkspaceSnapshot | None:
        """Capture the workspace state before execution for provenance."""
        try:
            return capture_workspace_snapshot(project_path)
        except Exception:
            logger.exception(
                "Failed to capture workspace snapshot before executing %s",
                self.waypoint.id,
            )
            return None

    def _log_workspace_provenance(
        self,
        project_path: Path,
        before_snapshot: WorkspaceSnapshot | None,
        iteration: int,
        result: str,
    ) -> WorkspaceDiffSummary | None:
        """Write a workspace diff summary into the execution log."""
        if before_snapshot is None or self._log_writer is None:
            return None
        try:
            after_snapshot = capture_workspace_snapshot(project_path)
            summary = summarize_workspace_diff(before_snapshot, after_snapshot)
            self._log_writer.log_workspace_diff(
                iteration=iteration,
                result=result,
                summary=summary.to_dict(),
            )
            return summary
        except Exception:
            logger.exception(
                "Failed to log workspace provenance for %s",
                self.waypoint.id,
            )
            return None

    def _persist_waypoint_memory(
        self,
        *,
        project_path: Path,
        result: str,
        iteration: int,
        reported_validation_commands: list[str],
        captured_criteria: dict[int, CriterionVerification],
        tool_validation_evidence: dict[str, CapturedEvidence],
        protocol_derailments: list[str],
        workspace_summary: WorkspaceDiffSummary | None,
        error_summary: str | None = None,
    ) -> None:
        """Persist waypoint execution memory for future waypoint retrieval."""
        try:
            verified_criteria = sorted(
                idx
                for idx, criterion in captured_criteria.items()
                if criterion.status == "verified"
            )
            validation_commands = tuple(dict.fromkeys(reported_validation_commands))
            useful_commands = tuple(
                command
                for command in dict.fromkeys(
                    [*reported_validation_commands, *tool_validation_evidence.keys()]
                )
                if command
            )
            changed_files: tuple[str, ...] = ()
            approx_tokens_changed: int | None = None
            if workspace_summary is not None:
                changed_files = tuple(
                    item.path for item in workspace_summary.top_changed_files
                )
                approx_tokens_changed = workspace_summary.approx_tokens_changed

            record = WaypointMemoryRecord(
                schema_version="v1",
                saved_at_utc=datetime.now(UTC).isoformat(),
                waypoint_id=self.waypoint.id,
                title=self.waypoint.title,
                objective=self.waypoint.objective,
                dependencies=tuple(self.waypoint.dependencies),
                result=result,
                iterations_used=iteration,
                max_iterations=self.max_iterations,
                protocol_derailments=tuple(protocol_derailments[-8:]),
                error_summary=error_summary,
                changed_files=changed_files,
                approx_tokens_changed=approx_tokens_changed,
                validation_commands=validation_commands,
                useful_commands=useful_commands[:8],
                verified_criteria=tuple(verified_criteria),
            )
            save_waypoint_memory(project_path, record)
        except Exception:
            logger.exception(
                "Failed to persist waypoint memory for %s",
                self.waypoint.id,
            )

    def _validate_no_external_changes(self, project_path: Path) -> list[str]:
        """Check if any files were modified outside the project directory.

        Returns a list of violation descriptions. Empty list means no violations.
        """
        violations: list[str] = []
        project_root = project_path.resolve()

        for file_op in self._file_operations:
            if file_op.tool_name not in ("Edit", "Write", "Read"):
                continue
            if not file_op.file_path:
                continue
            try:
                candidate = Path(file_op.file_path)
                resolved = (
                    candidate.resolve()
                    if candidate.is_absolute()
                    else (project_root / candidate).resolve()
                )
                if not resolved.is_relative_to(project_root):
                    violations.append(str(file_op.file_path))
            except OSError:
                violations.append(str(file_op.file_path))

        return violations


async def execute_waypoint(
    project: Project,
    waypoint: Waypoint,
    spec: str,
    on_progress: ProgressCallback | None = None,
    max_iterations: int = MAX_ITERATIONS,
    metrics_collector: "MetricsCollector | None" = None,
    host_validations_enabled: bool = True,
) -> ExecutionResult:
    """Convenience function to execute a single waypoint.

    Args:
        project: The project containing the waypoint
        waypoint: The waypoint to execute
        spec: The product specification for context
        on_progress: Optional callback for progress updates
        max_iterations: Maximum iterations before intervention (default 10)
        metrics_collector: Optional collector for recording LLM metrics
        host_validations_enabled: Whether to run host validations in finalize

    Returns:
        ExecutionResult indicating success, failure, or other outcomes

    Raises:
        InterventionNeededError: When execution fails and needs human intervention
    """
    executor = WaypointExecutor(
        project,
        waypoint,
        spec,
        on_progress,
        max_iterations=max_iterations,
        metrics_collector=metrics_collector,
        host_validations_enabled=host_validations_enabled,
    )
    return await executor.execute()
