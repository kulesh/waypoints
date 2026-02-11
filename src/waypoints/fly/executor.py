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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoints.config.app_root import dangerous_app_root
from waypoints.config.settings import settings
from waypoints.fly.escalation_policy import (
    build_escalation_decision,
    detect_protocol_issues,
)
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
from waypoints.fly.intervention_policy import classify_execution_error
from waypoints.fly.kickoff_prompt import (
    build_iteration_kickoff_prompt as _build_iteration_kickoff_prompt_payload,
)
from waypoints.fly.protocol import parse_stage_reports
from waypoints.fly.provenance import (
    WorkspaceDiffSummary,
    WorkspaceSnapshot,
    capture_workspace_snapshot,
    summarize_workspace_diff,
)
from waypoints.fly.stack import ValidationCommand
from waypoints.fly.types import (
    ExecutionContext,
    ExecutionResult,
    ExecutionStep,
    ProgressCallback,
    _LoopState,
)
from waypoints.git.config import Checklist
from waypoints.git.receipt import (
    CapturedEvidence,
    CriterionVerification,
)
from waypoints.llm.client import (
    StreamChunk,
    StreamComplete,
    StreamToolUse,
    agent_query,
)
from waypoints.llm.prompts import build_execution_prompt
from waypoints.memory import (
    ProjectMemoryIndex,
    WaypointMemoryRecord,
    build_waypoint_memory_context_details,
    format_directory_policy_for_prompt,
    load_or_build_project_memory,
    save_waypoint_memory,
)
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint
from waypoints.spec import compute_spec_hash

if TYPE_CHECKING:
    from waypoints.fly.receipt_finalizer import ReceiptFinalizer
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)

# Max iterations before giving up
MAX_ITERATIONS = 10
MAX_PROTOCOL_DERAILMENT_STREAK = 2


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
        self._waypoint_memory_ids: tuple[str, ...] = ()
        self._current_spec_hash: str | None = None
        self._spec_context_stale: bool = False

    def cancel(self) -> None:
        """Cancel the execution."""
        self._cancelled = True

    def log_pause_event(self) -> None:
        """Log a pause event when execution is temporarily halted."""
        if self._log_writer is not None:
            self._log_writer.log_pause()

    def log_git_commit_event(
        self,
        success: bool,
        commit_hash: str,
        message: str,
    ) -> None:
        """Log a git commit event associated with this execution."""
        if self._log_writer is not None:
            self._log_writer.log_git_commit(success, commit_hash, message)

    def log_intervention_resolved_event(self, action: str, **params: Any) -> None:
        """Log that an intervention was resolved and execution resumed/paused."""
        if self._log_writer is not None:
            self._log_writer.log_intervention_resolved(action, **params)

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
        """Internal implementation of execute, runs in project directory.

        Orchestrates iterations by delegating to focused methods:
        - _run_iteration: stream one agent turn, parse evidence
        - _handle_completion: finalize receipt, return or retry
        - _escalate_if_needed: check protocol derailments, stuck agent
        """
        self._log_writer = ExecutionLogWriter(self.project, self.waypoint)

        checklist = Checklist.load(self.project)
        self._validation_commands = self._resolve_validation_commands(
            project_path, checklist
        )
        self._refresh_project_memory(project_path)
        self._refresh_spec_context_status()

        prompt = build_execution_prompt(
            self.waypoint,
            self.spec,
            project_path,
            checklist,
            directory_policy_context=self._directory_policy_context,
            waypoint_memory_context=self._waypoint_memory_context,
            full_spec_pointer="docs/product-spec.md",
            spec_context_stale=self._spec_context_stale,
            current_spec_hash=self._current_spec_hash,
        )

        logger.info(
            "Starting execution of %s: %s", self.waypoint.id, self.waypoint.title
        )
        logger.info("Execution log: %s", self._log_writer.file_path)

        s = _LoopState(
            workspace_before=self._capture_workspace_snapshot(project_path),
            prompt=prompt,
            completion_marker=(
                f"<waypoint-complete>{self.waypoint.id}</waypoint-complete>"
            ),
        )

        while s.iteration < self.max_iterations:
            if self._cancelled:
                return self._finish(project_path, s, ExecutionResult.CANCELLED)

            s.iteration += 1
            logger.info("Iteration %d/%d", s.iteration, self.max_iterations)
            self._report_progress(
                s.iteration,
                self.max_iterations,
                "executing",
                f"Iteration {s.iteration}",
            )

            iter_prompt = (
                s.prompt
                if s.iteration == 1
                else self._build_iteration_kickoff_prompt(
                    reason_code=s.next_reason_code,
                    reason_detail=s.next_reason_detail,
                    completion_marker=s.completion_marker,
                    captured_criteria=s.captured_criteria,
                )
            )

            self._log_iteration_start(s, iter_prompt)

            try:
                iteration_output, iteration_cost = await self._run_iteration(
                    project_path, s, iter_prompt
                )
            except Exception as e:
                self._handle_iteration_error(project_path, s, e)
                # _handle_iteration_error always raises
                raise  # unreachable, satisfies type checker

            if s.completion_detected:
                result = await self._handle_completion(
                    project_path, s, iteration_output, iteration_cost
                )
                if result is not None:
                    return result
                continue  # receipt failed, retry

            # Log output and check for escalation
            self._log_iteration_output(s, iteration_output, iteration_cost)
            self._escalate_if_needed(project_path, s, iteration_output)

        # Max iterations reached
        self._raise_max_iterations(project_path, s)
        raise AssertionError("unreachable")  # _raise_max_iterations always raises

    # ─── Iteration Helpers ────────────────────────────────────────────

    def _log_iteration_start(self, s: _LoopState, iter_prompt: str) -> None:
        """Log the start of an iteration with context metadata."""
        assert self._log_writer is not None
        is_first = s.iteration == 1
        self._log_writer.log_iteration_start(
            iteration=s.iteration,
            prompt=iter_prompt,
            reason_code=s.next_reason_code,
            reason_detail=s.next_reason_detail,
            resume_session_id=s.resume_session_id,
            memory_waypoint_ids=(list(self._waypoint_memory_ids) if is_first else None),
            memory_context_chars=(
                len(self._waypoint_memory_context or "") if is_first else None
            ),
            spec_context_summary_chars=(
                len(self.waypoint.spec_context_summary.strip()) if is_first else None
            ),
            spec_section_ref_count=(
                len(self.waypoint.spec_section_refs) if is_first else None
            ),
            spec_context_hash=(self.waypoint.spec_context_hash if is_first else None),
            current_spec_hash=(self._current_spec_hash if is_first else None),
            spec_context_stale=(self._spec_context_stale if is_first else None),
            full_spec_pointer=("docs/product-spec.md" if is_first else None),
        )

    async def _run_iteration(
        self,
        project_path: Path,
        s: _LoopState,
        iter_prompt: str,
    ) -> tuple[str, float | None]:
        """Run one agent iteration: stream chunks, parse evidence.

        Returns (iteration_output, iteration_cost).
        Raises on API/execution error (caught by caller).
        Updates s.full_output, s.captured_criteria, etc. in place.
        """
        assert self._log_writer is not None
        iteration_output = ""
        iteration_cost: float | None = None
        iteration_file_ops: list[FileOperation] = []
        s.iter_scope_drift_detected = False
        s.iter_stage_reports_logged = 0

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
            resume_session_id=s.resume_session_id,
            metrics_collector=self.metrics_collector,
            phase="fly",
            waypoint_id=self.waypoint.id,
        ):
            if isinstance(chunk, StreamChunk):
                iteration_output = self._handle_stream_text_chunk(
                    s=s,
                    chunk=chunk,
                    iteration_output=iteration_output,
                )

            elif isinstance(chunk, StreamToolUse):
                self._handle_stream_tool_use_chunk(
                    s=s,
                    chunk=chunk,
                    iteration_file_ops=iteration_file_ops,
                )

            elif isinstance(chunk, StreamComplete):
                iteration_cost = self._handle_stream_complete_chunk(
                    s=s,
                    chunk=chunk,
                )

        return iteration_output, iteration_cost

    def _handle_stream_text_chunk(
        self,
        *,
        s: _LoopState,
        chunk: StreamChunk,
        iteration_output: str,
    ) -> str:
        """Process a streamed text chunk and update completion/progress state."""
        assert self._log_writer is not None
        next_output = iteration_output + chunk.text
        s.full_output += chunk.text

        if not s.completion_detected:
            self._parse_evidence(s)
            self._parse_stage_reports(s)

            if s.completion_marker in next_output:
                logger.info("Completion marker found!")
                s.completion_detected = True
                s.completion_iteration = s.iteration
                s.completion_output = next_output
                criterion_matches = CRITERION_PATTERN.findall(next_output)
                s.completion_criteria = {int(m[0]) for m in criterion_matches}
                self._log_writer.log_completion_detected(s.iteration)

        if s.completion_detected:
            return next_output

        criterion_matches = CRITERION_PATTERN.findall(s.full_output)
        completed_indices = {int(m[0]) for m in criterion_matches}
        self._report_progress(
            s.iteration,
            self.max_iterations,
            "streaming",
            chunk.text,
            criteria_completed=completed_indices,
        )
        return next_output

    def _handle_stream_tool_use_chunk(
        self,
        *,
        s: _LoopState,
        chunk: StreamToolUse,
        iteration_file_ops: list[FileOperation],
    ) -> None:
        """Process a tool-use chunk and update evidence/progress state."""
        assert self._log_writer is not None
        s.last_tool_name = chunk.tool_name
        s.last_tool_input = dict(chunk.tool_input)
        s.last_tool_output = chunk.tool_output
        self._log_writer.log_tool_call(
            s.iteration,
            chunk.tool_name,
            chunk.tool_input,
            chunk.tool_output,
        )
        if (
            isinstance(chunk.tool_output, str)
            and "Error: Access denied:" in chunk.tool_output
        ):
            s.iter_scope_drift_detected = True
        if chunk.tool_name == "Bash":
            command = chunk.tool_input.get("command")
            if isinstance(command, str) and chunk.tool_output:
                category = _detect_validation_category(command)
                stdout, stderr, exit_code = _parse_tool_output(chunk.tool_output)
                evidence = CapturedEvidence(
                    command=command,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    captured_at=datetime.now(UTC),
                )
                s.tool_validation_evidence[_normalize_command(command)] = evidence
                if category:
                    s.tool_validation_categories[category] = evidence
        file_op = _extract_file_operation(chunk.tool_name, chunk.tool_input)
        if file_op:
            iteration_file_ops.append(file_op)
            self._file_operations.append(file_op)
            self._report_progress(
                s.iteration,
                self.max_iterations,
                "tool_use",
                f"{file_op.tool_name}: {file_op.file_path}",
                file_operations=iteration_file_ops,
            )
            return

        summary = f"{chunk.tool_name}: {chunk.tool_input}"
        if len(summary) > 300:
            summary = summary[:300] + "..."
        self._report_progress(
            s.iteration,
            self.max_iterations,
            "tool_use",
            summary,
        )

    def _handle_stream_complete_chunk(
        self,
        *,
        s: _LoopState,
        chunk: StreamComplete,
    ) -> float | None:
        """Process stream completion metadata and return iteration cost."""
        iteration_cost = chunk.cost_usd
        if chunk.session_id:
            s.resume_session_id = chunk.session_id
        logger.info(
            "Iteration %d complete, cost: $%.4f",
            s.iteration,
            chunk.cost_usd or 0,
        )
        return iteration_cost

    def _parse_evidence(self, s: _LoopState) -> None:
        """Parse validation and criterion evidence from accumulated output."""
        for match in VALIDATION_PATTERN.findall(s.full_output):
            command, _, _ = match
            normalized_command = command.strip()
            if not normalized_command:
                continue
            if normalized_command in s.reported_validation_commands:
                continue
            s.reported_validation_commands.append(normalized_command)
            category = _detect_validation_category(normalized_command)
            if category:
                logger.info(
                    "Model reported validation command for %s: %s",
                    category,
                    normalized_command,
                )

        for match in CRITERION_PATTERN.findall(s.full_output):
            index, status, text, evidence = match
            idx = int(index)
            if idx not in s.captured_criteria:
                s.captured_criteria[idx] = CriterionVerification(
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

    def _parse_stage_reports(self, s: _LoopState) -> None:
        """Parse structured stage reports from accumulated output."""
        assert self._log_writer is not None
        for report in parse_stage_reports(s.full_output):
            key = (
                report.stage,
                report.success,
                report.output,
                tuple(report.artifacts),
                report.next_stage,
            )
            if key in s.logged_stage_reports:
                continue
            s.logged_stage_reports.add(key)
            s.iter_stage_reports_logged += 1
            self._log_writer.log_stage_report(s.iteration, report)
            output = report.output.strip()
            if len(output) > 400:
                output = output[:400] + "..."
            summary = f"{report.stage.value}: {output}".strip()
            self._report_progress(
                s.iteration,
                self.max_iterations,
                "stage",
                summary,
            )

    async def _handle_completion(
        self,
        project_path: Path,
        s: _LoopState,
        iteration_output: str,
        iteration_cost: float | None,
    ) -> ExecutionResult | None:
        """Finalize after completion marker detected.

        Returns ExecutionResult.SUCCESS if receipt is valid, or None
        to signal the caller to retry (receipt failed).
        """
        assert self._log_writer is not None
        self.steps.append(
            ExecutionStep(
                iteration=s.completion_iteration or s.iteration,
                action="complete",
                output=s.completion_output or iteration_output,
            )
        )
        final_completed = s.completion_criteria or set()
        self._log_writer.log_output(
            s.iteration,
            s.completion_output or iteration_output,
            final_completed,
        )
        self._log_writer.log_iteration_end(s.iteration, iteration_cost)

        finalizer = self._make_finalizer()
        receipt_valid = await finalizer.finalize(
            project_path=project_path,
            captured_criteria=s.captured_criteria,
            validation_commands=self._validation_commands,
            reported_validation_commands=s.reported_validation_commands,
            tool_validation_evidence=s.tool_validation_evidence,
            tool_validation_categories=s.tool_validation_categories,
            host_validations_enabled=self.host_validations_enabled,
            max_iterations=self.max_iterations,
        )

        if receipt_valid:
            self._report_progress(
                s.iteration,
                MAX_ITERATIONS,
                "complete",
                "Waypoint complete with valid receipt!",
            )
            return self._finish(project_path, s, ExecutionResult.SUCCESS)

        # Receipt invalid — prepare retry
        failure_summary = "Receipt validation failed."
        if hasattr(finalizer, "last_failure_summary"):
            summary_func = getattr(finalizer, "last_failure_summary")
            if callable(summary_func):
                failure_summary = summary_func()
        logger.warning(
            "Receipt invalid for %s at iteration %d. Retrying: %s",
            self.waypoint.id,
            s.iteration,
            failure_summary,
        )
        self._report_progress(
            s.iteration,
            MAX_ITERATIONS,
            "validation_failed",
            f"Host validation failed; retrying. {failure_summary}",
        )
        self._log_writer.log_error(
            s.iteration,
            f"Completion marker emitted but receipt was invalid: {failure_summary}",
        )
        s.next_reason_code = "host_validation_failed"
        s.next_reason_detail = failure_summary
        s.completion_detected = False
        s.completion_iteration = None
        s.completion_output = None
        s.completion_criteria = None
        self.steps.append(
            ExecutionStep(
                iteration=s.iteration,
                action="receipt_retry",
                output=failure_summary,
            )
        )
        return None  # signal retry

    def _log_iteration_output(
        self,
        s: _LoopState,
        iteration_output: str,
        iteration_cost: float | None,
    ) -> None:
        """Log iteration output and update criteria after a non-completion iteration."""
        assert self._log_writer is not None
        final_criteria = CRITERION_PATTERN.findall(s.full_output)
        final_completed = {int(m[0]) for m in final_criteria}
        self._log_writer.log_output(s.iteration, iteration_output, final_completed)
        self._log_writer.log_iteration_end(s.iteration, iteration_cost)

    def _escalate_if_needed(
        self,
        project_path: Path,
        s: _LoopState,
        iteration_output: str,
    ) -> None:
        """Check for protocol derailments and stuck agent; raise if needed.

        Also records iteration step and determines next iteration reason.
        """
        assert self._log_writer is not None
        verified_criteria = {
            idx
            for idx, criterion in s.captured_criteria.items()
            if criterion.status == "verified"
        }
        decision = build_escalation_decision(
            iteration_output=iteration_output,
            completion_marker=s.completion_marker,
            stage_reports_logged=s.iter_stage_reports_logged,
            scope_drift_detected=s.iter_scope_drift_detected,
            current_derailment_streak=s.protocol_derailment_streak,
            acceptance_criteria=self.waypoint.acceptance_criteria,
            verified_criteria=verified_criteria,
            waypoint_id=self.waypoint.id,
            max_derailment_streak=MAX_PROTOCOL_DERAILMENT_STREAK,
        )

        s.protocol_derailments.extend(decision.protocol_issues)
        s.protocol_derailment_streak = decision.protocol_derailment_streak
        s.next_reason_code = decision.next_reason_code
        s.next_reason_detail = decision.next_reason_detail

        if decision.protocol_issues:
            self._log_writer.log_protocol_derailment(
                iteration=s.iteration,
                issues=decision.protocol_issues,
                action=(
                    "escalate_intervention"
                    if decision.should_escalate
                    else "nudge_and_retry"
                ),
            )

        if decision.should_escalate and decision.escalation_summary is not None:
            self._raise_intervention(
                project_path,
                s,
                InterventionType.EXECUTION_ERROR,
                decision.escalation_summary,
            )

        # Record step
        self.steps.append(
            ExecutionStep(
                iteration=s.iteration,
                action="iterate",
                output=iteration_output,
            )
        )

        # Check if agent is stuck or needs human help
        if self._needs_intervention(iteration_output):
            logger.info("Human intervention needed")
            reason = self._extract_intervention_reason(iteration_output)
            self._raise_intervention(
                project_path,
                s,
                InterventionType.USER_REQUESTED,
                reason,
            )

    def _handle_iteration_error(
        self,
        project_path: Path,
        s: _LoopState,
        e: Exception,
    ) -> None:
        """Classify an iteration exception and raise InterventionNeededError.

        Always raises — never returns.
        """
        assert self._log_writer is not None
        logger.exception("Error during iteration %d: %s", s.iteration, e)

        classification = classify_execution_error(e, full_output=s.full_output)
        error_summary = classification.error_summary

        if failed_command_summary := self._failed_command_summary(s):
            error_summary = f"{error_summary}\n\n{failed_command_summary}"

        self._report_progress(
            s.iteration,
            self.max_iterations,
            "error",
            f"Error: {error_summary}",
        )
        self.steps.append(
            ExecutionStep(
                iteration=s.iteration,
                action="error",
                output=str(e),
            )
        )
        self._log_writer.log_error(s.iteration, str(e))
        self._log_writer.log_completion(ExecutionResult.FAILED.value)

        intervention = Intervention(
            type=classification.intervention_type,
            waypoint=self.waypoint,
            iteration=s.iteration,
            max_iterations=self.max_iterations,
            error_summary=error_summary,
            context={
                "full_output": s.full_output[-2000:],
                "api_error_type": classification.api_error_type.value,
                "original_error": str(e),
                "last_tool_name": s.last_tool_name,
                "last_tool_input": s.last_tool_input,
                "last_tool_output_tail": self._truncate_output(s.last_tool_output),
                "configured_budget_usd": settings.llm_budget_usd,
                "current_cost_usd": (
                    self.metrics_collector.total_cost
                    if self.metrics_collector
                    else None
                ),
                "resume_at_utc": (
                    classification.reset_at.astimezone(UTC).isoformat()
                    if classification.reset_at
                    else None
                ),
            },
        )
        self._log_writer.log_intervention_needed(
            s.iteration,
            intervention.type.value,
            error_summary,
        )
        workspace_summary = self._log_workspace_provenance(
            project_path,
            s.workspace_before,
            s.iteration,
            ExecutionResult.INTERVENTION_NEEDED.value,
        )
        self._persist_waypoint_memory(
            project_path=project_path,
            result=ExecutionResult.INTERVENTION_NEEDED.value,
            iteration=s.iteration,
            reported_validation_commands=s.reported_validation_commands,
            captured_criteria=s.captured_criteria,
            tool_validation_evidence=s.tool_validation_evidence,
            protocol_derailments=s.protocol_derailments,
            workspace_summary=workspace_summary,
            error_summary=error_summary,
        )
        raise InterventionNeededError(intervention) from e

    def _failed_command_summary(self, s: _LoopState) -> str | None:
        """Build a concise failed-command summary for intervention surfacing."""
        if s.last_tool_name != "Bash":
            return None
        command = s.last_tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return None

        command_block = command.strip()
        if len(command_block) > 500:
            command_block = command_block[:500] + "..."
        details = [f"Failed command:\n{command_block}"]

        output_tail = self._truncate_output(s.last_tool_output)
        if output_tail:
            details.append(f"Last command output (tail):\n{output_tail}")

        return "\n\n".join(details)

    def _truncate_output(self, output: str | None) -> str | None:
        """Trim output to an intervention-friendly tail snippet."""
        if not output:
            return None
        cleaned = output.strip()
        if not cleaned:
            return None
        if len(cleaned) <= 800:
            return cleaned
        return f"...{cleaned[-800:]}"

    def _raise_intervention(
        self,
        project_path: Path,
        s: _LoopState,
        intervention_type: InterventionType,
        error_summary: str,
    ) -> None:
        """Log and raise an InterventionNeededError. Always raises."""
        assert self._log_writer is not None
        self._log_writer.log_error(s.iteration, error_summary)
        self._log_writer.log_completion(ExecutionResult.INTERVENTION_NEEDED.value)
        intervention = Intervention(
            type=intervention_type,
            waypoint=self.waypoint,
            iteration=s.iteration,
            max_iterations=self.max_iterations,
            error_summary=error_summary,
            context={
                "full_output": s.full_output[-2000:],
                "reason_code": s.next_reason_code,
            },
        )
        self._log_writer.log_intervention_needed(
            s.iteration,
            intervention.type.value,
            error_summary,
        )
        workspace_summary = self._log_workspace_provenance(
            project_path,
            s.workspace_before,
            s.iteration,
            ExecutionResult.INTERVENTION_NEEDED.value,
        )
        self._persist_waypoint_memory(
            project_path=project_path,
            result=ExecutionResult.INTERVENTION_NEEDED.value,
            iteration=s.iteration,
            reported_validation_commands=s.reported_validation_commands,
            captured_criteria=s.captured_criteria,
            tool_validation_evidence=s.tool_validation_evidence,
            protocol_derailments=s.protocol_derailments,
            workspace_summary=workspace_summary,
            error_summary=error_summary,
        )
        raise InterventionNeededError(intervention)

    def _raise_max_iterations(self, project_path: Path, s: _LoopState) -> None:
        """Handle max iterations exhausted. Always raises."""
        logger.warning(
            "Max iterations (%d) reached without completion",
            self.max_iterations,
        )
        error_msg = (
            f"Waypoint did not complete after {self.max_iterations} iterations. "
            "The agent may be stuck or the task may be too complex."
        )
        self._raise_intervention(
            project_path,
            s,
            InterventionType.ITERATION_LIMIT,
            error_msg,
        )

    def _finish(
        self,
        project_path: Path,
        s: _LoopState,
        result: ExecutionResult,
    ) -> ExecutionResult:
        """Log completion, workspace provenance, memory, and return result."""
        assert self._log_writer is not None
        self._log_writer.log_completion(result.value)
        workspace_summary = self._log_workspace_provenance(
            project_path,
            s.workspace_before,
            s.iteration,
            result.value,
        )
        self._persist_waypoint_memory(
            project_path=project_path,
            result=result.value,
            iteration=s.iteration,
            reported_validation_commands=s.reported_validation_commands,
            captured_criteria=s.captured_criteria,
            tool_validation_evidence=s.tool_validation_evidence,
            protocol_derailments=s.protocol_derailments,
            workspace_summary=workspace_summary,
        )
        return result

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
        return _build_iteration_kickoff_prompt_payload(
            reason_code=reason_code,
            reason_detail=reason_detail,
            completion_marker=completion_marker,
            waypoint_id=self.waypoint.id,
            waypoint_title=self.waypoint.title,
            waypoint_objective=self.waypoint.objective,
            acceptance_criteria=self.waypoint.acceptance_criteria,
            verified_criteria=verified_criteria,
            spec_context_summary=self.waypoint.spec_context_summary.strip(),
            spec_section_refs=self.waypoint.spec_section_refs,
            waypoint_spec_hash=self.waypoint.spec_context_hash,
            current_spec_hash=self._current_spec_hash,
            spec_context_stale=self._spec_context_stale,
        )

    def _detect_protocol_issues(
        self,
        iteration_output: str,
        completion_marker: str,
        stage_reports_logged: int,
        scope_drift_detected: bool,
    ) -> list[str]:
        """Detect recoverable protocol issues for iteration-to-iteration nudging."""
        return detect_protocol_issues(
            iteration_output=iteration_output,
            completion_marker=completion_marker,
            stage_reports_logged=stage_reports_logged,
            scope_drift_detected=scope_drift_detected,
            waypoint_id=self.waypoint.id,
        )

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
            memory_context = build_waypoint_memory_context_details(
                project_root=project_path,
                waypoint=self.waypoint,
            )
            self._waypoint_memory_context = memory_context.text or None
            self._waypoint_memory_ids = memory_context.waypoint_ids
        except Exception:
            logger.exception(
                "Failed to build waypoint memory context for %s",
                self.waypoint.id,
            )
            self._waypoint_memory_context = None
            self._waypoint_memory_ids = ()

    def _refresh_spec_context_status(self) -> None:
        """Compute spec context freshness metadata for prompting and logs."""
        spec_text = self.spec.strip()
        self._current_spec_hash = compute_spec_hash(spec_text) if spec_text else None
        waypoint_hash = self.waypoint.spec_context_hash
        self._spec_context_stale = bool(
            self._current_spec_hash
            and waypoint_hash
            and waypoint_hash != self._current_spec_hash
        )
        if self._spec_context_stale:
            logger.warning(
                "Waypoint spec context appears stale for %s (waypoint=%s current=%s)",
                self.waypoint.id,
                waypoint_hash,
                self._current_spec_hash,
            )

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


__all__ = [
    "ExecutionContext",
    "ExecutionResult",
    "ExecutionStep",
    "WaypointExecutor",
    "execute_waypoint",
]
