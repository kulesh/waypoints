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
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from waypoints.config.app_root import dangerous_app_root
from waypoints.fly.execution_log import ExecutionLogWriter
from waypoints.fly.intervention import (
    Intervention,
    InterventionNeededError,
    InterventionType,
)
from waypoints.fly.protocol import parse_stage_reports
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
    extract_reset_time,
)
from waypoints.llm.prompts import build_execution_prompt
from waypoints.llm.providers.base import (
    BUDGET_PATTERNS,
    RATE_LIMIT_PATTERNS,
    UNAVAILABLE_PATTERNS,
)
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)

# Max iterations before giving up
MAX_ITERATIONS = 10

# Pattern to detect acceptance criterion verification markers in agent output
# Model outputs nested elements for reliable parsing:
#   <acceptance-criterion><index>N</index><status>verified|failed</status>
#   <text>...</text><evidence>...</evidence></acceptance-criterion>
CRITERION_PATTERN = re.compile(
    r"<acceptance-criterion>\s*"
    r"<index>(\d+)</index>\s*"
    r"<status>(verified|failed)</status>\s*"
    r"<text>(.*?)</text>\s*"
    r"<evidence>(.*?)</evidence>\s*"
    r"</acceptance-criterion>",
    re.DOTALL,
)

# Pattern to detect validation evidence markers in agent output
# Model outputs these when running tests, linting, formatting
VALIDATION_PATTERN = re.compile(
    r"<validation>\s*"
    r"<command>(.*?)</command>\s*"
    r"<exit-code>(\d+)</exit-code>\s*"
    r"<output>(.*?)</output>\s*"
    r"</validation>",
    re.DOTALL,
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
class FileOperation:
    """A file operation performed by the agent."""

    tool_name: str  # "Edit", "Write", "Read", "Bash", "Glob", "Grep"
    file_path: str | None
    line_number: int | None = None


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


def _extract_file_operation(
    tool_name: str, tool_input: dict[str, object]
) -> FileOperation | None:
    """Extract file operation from tool input.

    Args:
        tool_name: Name of the tool (Edit, Write, Read, Bash, etc.)
        tool_input: The tool input dict containing parameters

    Returns:
        FileOperation if a file path was found, None otherwise
    """
    if tool_name in ("Edit", "Write", "Read"):
        # These tools have file_path parameter
        path = tool_input.get("file_path")
        if isinstance(path, str):
            return FileOperation(tool_name=tool_name, file_path=path)
    elif tool_name == "Glob":
        # Glob has pattern, but we might want to show the pattern
        pattern = tool_input.get("pattern")
        if isinstance(pattern, str):
            return FileOperation(tool_name=tool_name, file_path=pattern)
    elif tool_name == "Grep":
        # Grep might have a path parameter
        path = tool_input.get("path")
        if isinstance(path, str):
            return FileOperation(tool_name=tool_name, file_path=path)
    elif tool_name == "Bash":
        # For bash, we could show the command (truncated)
        command = tool_input.get("command")
        if isinstance(command, str):
            # Truncate long commands
            display = command[:60] + "..." if len(command) > 60 else command
            return FileOperation(tool_name=tool_name, file_path=display)
    return None


def _detect_validation_category(command: str) -> str | None:
    """Detect validation category from command string.

    Args:
        command: The shell command that was run

    Returns:
        Category name (tests, linting, formatting) or None if not recognized
    """
    cmd_lower = command.lower()

    # Test commands
    if any(
        pattern in cmd_lower
        for pattern in ["test", "pytest", "jest", "mocha", "go test", "cargo test"]
    ):
        return "tests"

    if "ruff format" in cmd_lower or "ruff fmt" in cmd_lower:
        return "formatting"

    # Linting commands
    if any(
        pattern in cmd_lower
        for pattern in ["clippy", "ruff", "eslint", "lint", "pylint", "flake8"]
    ):
        return "linting"

    # Formatting commands
    if any(
        pattern in cmd_lower for pattern in ["fmt", "format", "prettier", "rustfmt"]
    ):
        return "formatting"

    # Type checking commands
    if any(pattern in cmd_lower for pattern in ["mypy", "tsc", "typecheck", "pyright"]):
        return "type checking"

    return None


def _normalize_command(command: str) -> str:
    """Normalize a shell command for matching."""
    return " ".join(command.strip().split())


def _parse_tool_output(output: str) -> tuple[str, str, int]:
    """Parse tool output into stdout, stderr, and exit code."""
    if not output:
        return "", "", 0

    exit_code = 0
    exit_match = re.search(r"\nExit code: (\d+)\s*$", output)
    if exit_match:
        exit_code = int(exit_match.group(1))
        output = output[: exit_match.start()]

    stdout = output
    stderr = ""
    if "\nSTDERR:\n" in output:
        stdout, stderr = output.split("\nSTDERR:\n", 1)

    return stdout.rstrip(), stderr.rstrip(), exit_code


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
        # Load checklist from project (creates default if not exists)
        checklist = Checklist.load(self.project)
        self._validation_commands = self._resolve_validation_commands(
            project_path, checklist
        )

        prompt = build_execution_prompt(
            self.waypoint, self.spec, project_path, checklist
        )

        logger.info(
            "Starting execution of %s: %s", self.waypoint.id, self.waypoint.title
        )

        # Initialize execution log
        self._log_writer = ExecutionLogWriter(self.project, self.waypoint)
        logger.info("Execution log: %s", self._log_writer.file_path)

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

        while iteration < self.max_iterations:
            if self._cancelled:
                logger.info("Execution cancelled")
                self._log_writer.log_completion(ExecutionResult.CANCELLED.value)
                return ExecutionResult.CANCELLED

            iteration += 1
            logger.info("Iteration %d/%d", iteration, self.max_iterations)

            self._report_progress(
                iteration, self.max_iterations, "executing", f"Iteration {iteration}"
            )

            # Log iteration start
            iter_prompt = prompt if iteration == 1 else "Continue implementing."
            self._log_writer.log_iteration_start(iteration, iter_prompt)

            # Run agent query with file and bash tools
            iteration_output = ""
            iteration_cost: float | None = None
            iteration_file_ops: list[FileOperation] = []
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

                    receipt_valid = await self._finalize_and_verify_receipt(
                        project_path,
                        captured_criteria,
                        self._validation_commands,
                        reported_validation_commands,
                        tool_validation_evidence=tool_validation_evidence,
                        tool_validation_categories=tool_validation_categories,
                        host_validations_enabled=self.host_validations_enabled,
                    )

                    if receipt_valid:
                        self._report_progress(
                            iteration,
                            MAX_ITERATIONS,
                            "complete",
                            "Waypoint complete with valid receipt!",
                        )
                        self._log_writer.log_completion(ExecutionResult.SUCCESS.value)
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
                        "Claude API rate limit reached. Wait a few minutes and retry."
                    ),
                    APIErrorType.API_UNAVAILABLE: (
                        "Claude service temporarily unavailable. Try again shortly."
                    ),
                    APIErrorType.BUDGET_EXCEEDED: (
                        "Claude usage limit exceeded. "
                        "Execution paused until budget resets."
                    ),
                }
                error_summary = error_summaries.get(api_error_type, str(e))

                # For budget errors, try to extract and include reset time
                if api_error_type == APIErrorType.BUDGET_EXCEEDED:
                    reset_time = extract_reset_time(str(e))
                    if reset_time:
                        error_summary = (
                            f"Claude usage limit exceeded. Resets {reset_time}."
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
                    },
                )
                self._log_writer.log_intervention_needed(
                    iteration, intervention.type.value, error_summary
                )
                raise InterventionNeededError(intervention) from e

            # Parse final criteria state and log iteration output
            final_criteria = CRITERION_PATTERN.findall(full_output)
            final_completed = {int(m[0]) for m in final_criteria}
            self._log_writer.log_output(iteration, iteration_output, final_completed)
            self._log_writer.log_iteration_end(iteration, iteration_cost)

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
        raise InterventionNeededError(intervention)

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the agent."""
        project_path = self.project.get_path()
        return f"""You are implementing a software waypoint as part of a larger project.
You have access to file and bash tools to read, write, and execute code.

**CRITICAL CONSTRAINTS:**
- Your working directory is: {project_path}
- ONLY access files within this directory
- NEVER use absolute paths outside the project
- NEVER use ../ to escape the project directory

Work methodically:
1. First understand the existing codebase
2. Make minimal, focused changes
3. Test after each change
4. Iterate until done

When complete, output the completion marker specified in the instructions."""

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

    def _make_finalizer(self):  # noqa: ANN202
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

    async def _finalize_and_verify_receipt(
        self,
        project_path: Path,
        captured_criteria: dict[int, CriterionVerification],
        validation_commands: list[ValidationCommand],
        reported_validation_commands: list[str],
        tool_validation_evidence: dict[str, CapturedEvidence] | None = None,
        tool_validation_categories: dict[str, CapturedEvidence] | None = None,
        host_validations_enabled: bool = True,
    ) -> bool:
        """Build receipt from host-captured evidence and verify with LLM.

        Delegates to ReceiptFinalizer for the actual finalization pipeline.
        """
        finalizer = self._make_finalizer()
        return await finalizer.finalize(
            project_path=project_path,
            captured_criteria=captured_criteria,
            validation_commands=validation_commands,
            reported_validation_commands=reported_validation_commands,
            tool_validation_evidence=tool_validation_evidence,
            tool_validation_categories=tool_validation_categories,
            host_validations_enabled=host_validations_enabled,
            max_iterations=self.max_iterations,
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
