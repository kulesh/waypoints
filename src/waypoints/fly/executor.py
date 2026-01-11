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
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from waypoints.fly.execution_log import ExecutionLogWriter
from waypoints.fly.intervention import (
    Intervention,
    InterventionNeededError,
    InterventionType,
)
from waypoints.git.config import Checklist
from waypoints.llm.client import (
    APIErrorType,
    StreamChunk,
    StreamComplete,
    StreamToolUse,
    agent_query,
    classify_api_error,
)
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint

if TYPE_CHECKING:
    from waypoints.llm.metrics import MetricsCollector

logger = logging.getLogger(__name__)

# Max iterations before giving up
MAX_ITERATIONS = 10

# Pattern to detect criterion completion markers in agent output
CRITERION_PATTERN = re.compile(
    r'<criterion-verified index="(\d+)">(.+?)</criterion-verified>'
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


def _build_prompt(
    waypoint: Waypoint,
    spec: str,
    project_path: Path,
    checklist: Checklist,
) -> str:
    """Build the execution prompt for a waypoint."""
    # Format criteria with indices for tracking
    criteria_list = "\n".join(
        f"- [ ] [{i}] {c}" for i, c in enumerate(waypoint.acceptance_criteria)
    )

    checklist_items = "\n".join(f"- {item}" for item in checklist.items)
    # Normalize waypoint ID for receipt filename
    safe_wp_id = waypoint.id.lower().replace("-", "")

    return f"""## Current Waypoint: {waypoint.id}
{waypoint.title}

## Objective
{waypoint.objective}

## Acceptance Criteria (must all pass)
{criteria_list}

**Progress Tracking:** When you verify each criterion, output a marker:
<criterion-verified index="N">criterion text</criterion-verified>

## Product Spec Summary
{spec[:2000]}{"..." if len(spec) > 2000 else ""}

## Working Directory
{project_path}

## Instructions
You are implementing a software waypoint. Your task is to:

1. Read any existing code in the project to understand the codebase
2. Create/modify code files to achieve the waypoint objective
3. Write tests that verify the acceptance criteria
4. Run tests with `pytest -v` and ensure they pass
5. If tests fail, analyze the failure and fix the code
6. Iterate until all acceptance criteria are met

**CRITICAL SAFETY RULES:**
- **STAY IN THE PROJECT**: Only read/write files within {project_path}
- **NEVER** use absolute paths starting with /Users, /home, /tmp, or similar
- **NEVER** access parent directories with ../ to escape the project
- **NEVER** modify files outside the project directory
- All file operations MUST be relative to the project root
- Violations will cause immediate termination and rollback

**Implementation Guidelines:**
- If the project is empty, that's expected - build from scratch using the spec
- Work iteratively - read, write, test, fix
- Keep changes minimal and focused on the waypoint objective
- Follow existing code patterns and style in the project
- Create tests before or alongside implementation
- Run tests after each significant change

## Pre-Completion Checklist
Before marking this waypoint complete, verify the following:
{checklist_items}

For each item, interpret it conceptually based on this project's technology stack.
For example, "Code passes linting" might mean running `ruff check .` for Python.

## Checklist Receipt
After verifying the checklist, produce a receipt file at:
`.waypoints/projects/[project-slug]/receipts/{safe_wp_id}-[timestamp].json`

The receipt must contain:
```json
{{
  "waypoint_id": "{waypoint.id}",
  "completed_at": "[ISO timestamp]",
  "checklist": [
    {{
      "item": "Code passes linting",
      "status": "passed",
      "evidence": "Ran ruff check . - 0 errors"
    }},
    {{
      "item": "All tests pass",
      "status": "passed",
      "evidence": "Ran pytest - 10 passed"
    }}
  ]
}}
```

Status options: "passed", "failed", "skipped" (with "reason" field if skipped).

**COMPLETION SIGNAL:**
When ALL acceptance criteria are met, checklist verified, and receipt produced, output:
<waypoint-complete>{waypoint.id}</waypoint-complete>

Only output the completion marker when you are confident the waypoint is done.
If you cannot complete the waypoint after several attempts, explain what's blocking you.

Begin implementing this waypoint now.
"""


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
    ) -> None:
        self.project = project
        self.waypoint = waypoint
        self.spec = spec
        self.on_progress = on_progress
        self.max_iterations = max_iterations
        self.metrics_collector = metrics_collector
        self.steps: list[ExecutionStep] = []
        self._cancelled = False
        self._log_writer: ExecutionLogWriter | None = None

    def cancel(self) -> None:
        """Cancel the execution."""
        self._cancelled = True

    async def execute(self) -> ExecutionResult:
        """Execute the waypoint using iterative agentic loop.

        Returns the execution result (success, failed, max_iterations, etc.)
        """
        project_path = self.project.get_path()

        # Defense in depth: ensure we're in the project directory
        original_cwd = os.getcwd()
        os.chdir(project_path)
        try:
            result = await self._execute_impl(project_path)

            # Post-execution validation: check for escapes
            violations = self._validate_no_external_changes()
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

        prompt = _build_prompt(self.waypoint, self.spec, project_path, checklist)

        logger.info(
            "Starting execution of %s: %s", self.waypoint.id, self.waypoint.title
        )

        # Initialize execution log
        self._log_writer = ExecutionLogWriter(self.project, self.waypoint)
        logger.info("Execution log: %s", self._log_writer.file_path)

        iteration = 0
        full_output = ""
        completion_marker = f"<waypoint-complete>{self.waypoint.id}</waypoint-complete>"

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
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                    cwd=str(project_path),
                    metrics_collector=self.metrics_collector,
                    phase="fly",
                    waypoint_id=self.waypoint.id,
                ):
                    if isinstance(chunk, StreamChunk):
                        iteration_output += chunk.text
                        full_output += chunk.text

                        # Check for completion marker
                        if completion_marker in full_output:
                            logger.info("Completion marker found!")
                            self._log_writer.log_completion_detected(iteration)
                            self.steps.append(
                                ExecutionStep(
                                    iteration=iteration,
                                    action="complete",
                                    output=iteration_output,
                                )
                            )
                            # Parse criterion markers for logging
                            criterion_matches = CRITERION_PATTERN.findall(full_output)
                            final_completed = {int(m[0]) for m in criterion_matches}
                            self._log_writer.log_output(
                                iteration, iteration_output, final_completed
                            )
                            self._log_writer.log_iteration_end(
                                iteration, iteration_cost
                            )

                            # Run finalize step to verify receipt
                            receipt_valid = await self._finalize_and_verify_receipt(
                                project_path
                            )

                            if receipt_valid:
                                self._report_progress(
                                    iteration,
                                    MAX_ITERATIONS,
                                    "complete",
                                    "Waypoint complete with valid receipt!",
                                )
                                self._log_writer.log_completion(
                                    ExecutionResult.SUCCESS.value
                                )
                                return ExecutionResult.SUCCESS
                            else:
                                # Receipt missing/invalid - warn but still succeed
                                # (code trusts but logs the issue)
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
                                self._log_writer.log_completion(
                                    ExecutionResult.SUCCESS.value
                                )
                                return ExecutionResult.SUCCESS

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
                            None,  # Output not available from streaming
                        )
                        # Extract and track file operation
                        file_op = _extract_file_operation(
                            chunk.tool_name, chunk.tool_input
                        )
                        if file_op:
                            iteration_file_ops.append(file_op)
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

            except Exception as e:
                logger.exception("Error during iteration %d: %s", iteration, e)

                # Classify the error for better user feedback
                api_error_type = classify_api_error(e)

                # Map API error type to intervention type
                intervention_type = {
                    APIErrorType.RATE_LIMITED: InterventionType.RATE_LIMITED,
                    APIErrorType.API_UNAVAILABLE: InterventionType.API_UNAVAILABLE,
                    APIErrorType.BUDGET_EXCEEDED: InterventionType.BUDGET_EXCEEDED,
                }.get(api_error_type, InterventionType.EXECUTION_ERROR)

                # Create user-friendly error summary
                error_summaries = {
                    APIErrorType.RATE_LIMITED: (
                        "Claude API rate limit reached. "
                        "Wait a few minutes and retry."
                    ),
                    APIErrorType.API_UNAVAILABLE: (
                        "Claude service temporarily unavailable. " "Try again shortly."
                    ),
                    APIErrorType.BUDGET_EXCEEDED: (
                        "Daily Claude budget exceeded. "
                        "Execution paused until budget resets."
                    ),
                }
                error_summary = error_summaries.get(api_error_type, str(e))

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

    async def _finalize_and_verify_receipt(self, project_path: Path) -> bool:
        """Run finalize step to ensure receipt is produced.

        This is Step 2 of the model guidance - after completion marker,
        we remind the model about the receipt and verify it exists.

        Returns True if receipt is valid, False otherwise.
        """
        from waypoints.git.receipt import ReceiptValidator

        validator = ReceiptValidator()
        receipt_path = validator.find_latest_receipt(self.project, self.waypoint.id)

        if receipt_path:
            result = validator.validate(receipt_path)
            if result.valid:
                logger.info("Receipt already exists and is valid: %s", receipt_path)
                return True
            else:
                logger.warning("Receipt exists but invalid: %s", result.message)

        # Receipt missing or invalid - send finalize prompt
        logger.info("Sending finalize prompt to ensure receipt is produced")

        # Normalize waypoint ID for receipt filename
        safe_wp_id = self.waypoint.id.lower().replace("-", "")

        finalize_prompt = f"""Waypoint complete. Produce the checklist receipt.

**Required:** Create a JSON receipt at:
`.waypoints/projects/{self.project.slug}/receipts/{safe_wp_id}-{{timestamp}}.json`

Run the pre-completion checklist and record results:
1. Linting (e.g., `ruff check .`)
2. Tests (e.g., `pytest`)
3. Type checking (e.g., `mypy src/`)
4. Formatting (e.g., `black --check .`)

Receipt structure:
```json
{{
  "waypoint_id": "{self.waypoint.id}",
  "completed_at": "[ISO timestamp]",
  "checklist": [
    {{
      "item": "Code passes linting",
      "status": "passed|failed|skipped",
      "evidence": "..."
    }}
  ]
}}
```

Use "skipped" with "reason" if a check is not applicable.
Create the receipt now.
"""

        self._report_progress(
            self.max_iterations,
            self.max_iterations,
            "finalizing",
            "Verifying checklist...",
        )

        # Log finalize phase start
        assert self._log_writer is not None  # Guaranteed by _execute_impl
        self._log_writer.log_finalize_start()

        finalize_output = ""
        try:
            async for chunk in agent_query(
                prompt=finalize_prompt,
                system_prompt="Finalize waypoint. Produce the checklist receipt.",
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                cwd=str(project_path),
                metrics_collector=self.metrics_collector,
                phase="fly",
                waypoint_id=self.waypoint.id,
            ):
                if isinstance(chunk, StreamChunk):
                    finalize_output += chunk.text
                    self._report_progress(
                        self.max_iterations,
                        self.max_iterations,
                        "finalizing",
                        chunk.text,
                    )
                elif isinstance(chunk, StreamToolUse):
                    # Log finalize phase tool calls
                    self._log_writer.log_finalize_tool_call(
                        chunk.tool_name,
                        chunk.tool_input,
                        None,
                    )
        except Exception as e:
            logger.error("Error during finalize: %s", e)
            self._log_writer.log_error(0, f"Finalize error: {e}")
            return False

        # Log finalize output
        if finalize_output:
            self._log_writer.log_finalize_output(finalize_output)
        self._log_writer.log_finalize_end()

        # Check for receipt again
        receipt_path = validator.find_latest_receipt(self.project, self.waypoint.id)
        if receipt_path:
            result = validator.validate(receipt_path)
            self._log_writer.log_receipt_validated(
                str(receipt_path), result.valid, result.message
            )
            if result.valid:
                logger.info("Receipt created and validated: %s", receipt_path)
                return True
            else:
                logger.warning("Receipt invalid after finalize: %s", result.message)
                return False

        logger.warning("No receipt found after finalize prompt")
        self._log_writer.log_receipt_validated("", False, "No receipt found")
        return False

    def _validate_no_external_changes(self) -> list[str]:
        """Check if any files were modified outside the project directory.

        Returns a list of violation descriptions. Empty list means no violations.
        """
        violations = []

        # Get the waypoints app directory (where this code lives)
        waypoints_app_dir = Path(__file__).parent.parent.parent.parent

        try:
            # Check git status of waypoints app directory
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=waypoints_app_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.stdout.strip():
                # There are changes in the waypoints app
                changed_files = result.stdout.strip()
                violations.append(f"Waypoints app directory modified:\n{changed_files}")
                logger.warning(
                    "Agent modified files outside project! Changes:\n%s",
                    changed_files,
                )
        except subprocess.SubprocessError as e:
            logger.debug("Could not check waypoints app git status: %s", e)

        return violations


async def execute_waypoint(
    project: Project,
    waypoint: Waypoint,
    spec: str,
    on_progress: ProgressCallback | None = None,
    max_iterations: int = MAX_ITERATIONS,
    metrics_collector: "MetricsCollector | None" = None,
) -> ExecutionResult:
    """Convenience function to execute a single waypoint.

    Args:
        project: The project containing the waypoint
        waypoint: The waypoint to execute
        spec: The product specification for context
        on_progress: Optional callback for progress updates
        max_iterations: Maximum iterations before intervention (default 10)
        metrics_collector: Optional collector for recording LLM metrics

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
    )
    return await executor.execute()
