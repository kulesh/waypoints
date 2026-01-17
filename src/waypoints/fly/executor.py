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
from waypoints.fly.stack import (
    STACK_COMMANDS,
    StackConfig,
    build_validation_section,
    detect_stack,
    detect_stack_from_spec,
)
from waypoints.git.config import Checklist
from waypoints.git.receipt import CapturedEvidence, ReceiptBuilder
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
    stack_configs: list[StackConfig],
) -> str:
    """Build the execution prompt for a waypoint."""
    # Format criteria with indices for tracking
    criteria_list = "\n".join(
        f"- [ ] [{i}] {c}" for i, c in enumerate(waypoint.acceptance_criteria)
    )

    checklist_items = "\n".join(f"- {item}" for item in checklist.items)
    # Normalize waypoint ID for receipt filename
    safe_wp_id = waypoint.id.lower().replace("-", "")

    # Build stack-specific validation section
    validation_section = build_validation_section(
        stack_configs, checklist.validation_overrides
    )

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

## Validation Commands
{validation_section}

Run each validation command. If any fails:
1. Analyze the error output
2. Fix the underlying issue
3. Re-run the validation
4. Only mark complete when all validations pass

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

        # Detect technology stack from project files
        stack_configs = detect_stack(project_path)

        # Fallback to spec-based detection for greenfield projects
        if not stack_configs and self.spec:
            stack_types = detect_stack_from_spec(self.spec)
            stack_configs = [
                StackConfig(st, list(STACK_COMMANDS.get(st, [])))
                for st in stack_types
            ]

        if stack_configs:
            stack_names = [c.stack_type.value for c in stack_configs]
            logger.info("Detected stacks: %s", ", ".join(stack_names))

        prompt = _build_prompt(
            self.waypoint, self.spec, project_path, checklist, stack_configs
        )

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
                                project_path, stack_configs, checklist
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

    def _run_validation_command(
        self,
        command: str,
        project_path: Path,
    ) -> CapturedEvidence:
        """Run a validation command and capture its output.

        Args:
            command: The shell command to run
            project_path: Working directory for the command

        Returns:
            CapturedEvidence with command results
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return CapturedEvidence(
                command=command,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                captured_at=datetime.now(),
            )
        except subprocess.TimeoutExpired:
            return CapturedEvidence(
                command=command,
                exit_code=-1,
                stdout="",
                stderr="Command timed out after 120s",
                captured_at=datetime.now(),
            )
        except Exception as e:
            return CapturedEvidence(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                captured_at=datetime.now(),
            )

    def _build_verification_prompt(self, receipt_path: Path) -> str:
        """Build the LLM verification prompt for a receipt.

        Args:
            receipt_path: Path to the receipt file

        Returns:
            Verification prompt string
        """
        from waypoints.git.receipt import ChecklistReceipt

        receipt = ChecklistReceipt.load(receipt_path)

        # Build context section
        context_section = ""
        if receipt.context:
            criteria_list = "\n".join(
                f"  - {c}" for c in receipt.context.acceptance_criteria
            )
            context_section = f"""### Waypoint Context
- **Title**: {receipt.context.title}
- **Objective**: {receipt.context.objective}
- **Acceptance Criteria**:
{criteria_list}

"""

        # Build evidence section
        evidence_sections = []
        for item in receipt.checklist:
            status_emoji = "✅" if item.status == "passed" else "❌"
            output = item.stdout or item.stderr or "(no output)"
            # Truncate long outputs
            if len(output) > 500:
                output = output[:500] + "\n... (truncated)"
            evidence_sections.append(
                f"""**{item.item}** {status_emoji}
- Command: `{item.command}`
- Exit code: {item.exit_code}
- Output:
```
{output}
```"""
            )

        evidence_text = "\n\n".join(evidence_sections)

        return f"""## Receipt Verification

A receipt was generated for waypoint {receipt.waypoint_id}. Please verify it.

{context_section}### Captured Evidence

{evidence_text}

### Verification Task

Review the captured evidence and answer:
1. Did all checklist commands succeed (exit code 0)?
2. Does the output indicate genuine success (not empty, no hidden errors)?
3. Based on the evidence, is this waypoint complete?

Output your verdict:
<receipt-verdict status="valid|invalid">
Brief reasoning here
</receipt-verdict>
"""

    async def _finalize_and_verify_receipt(
        self,
        project_path: Path,
        stack_configs: list[StackConfig],
        checklist: Checklist,
    ) -> bool:
        """Run validation commands, build receipt, and verify with LLM.

        This captures real evidence by running validation commands ourselves,
        then asks the model to verify the receipt.

        Args:
            project_path: Project working directory
            stack_configs: Detected technology stacks
            checklist: Checklist configuration with overrides

        Returns True if receipt is valid, False otherwise.
        """
        assert self._log_writer is not None  # Guaranteed by _execute_impl
        self._log_writer.log_finalize_start()

        self._report_progress(
            self.max_iterations,
            self.max_iterations,
            "finalizing",
            "Running validation commands...",
        )

        # Build receipt with waypoint context
        receipt_builder = ReceiptBuilder(
            waypoint_id=self.waypoint.id,
            title=self.waypoint.title,
            objective=self.waypoint.objective,
            acceptance_criteria=self.waypoint.acceptance_criteria,
        )

        # Run validation commands for each detected stack
        for config in stack_configs:
            for cmd in config.commands:
                # Apply user overrides if present
                actual_command = checklist.validation_overrides.get(
                    cmd.category, cmd.command
                )
                logger.info("Running validation: %s (%s)", cmd.name, actual_command)

                self._report_progress(
                    self.max_iterations,
                    self.max_iterations,
                    "finalizing",
                    f"Running {cmd.name}...",
                )

                evidence = self._run_validation_command(actual_command, project_path)
                receipt_builder.capture(cmd.name, evidence)

                # Log the captured evidence
                self._log_writer.log_finalize_tool_call(
                    "Bash",
                    {"command": actual_command},
                    f"exit_code={evidence.exit_code}",
                )

        # Build and save receipt
        if not receipt_builder.has_evidence():
            logger.warning("No validation evidence captured")
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated("", False, "No evidence captured")
            return False

        receipt = receipt_builder.build()
        receipts_dir = self.project.get_path() / "receipts"
        receipts_dir.mkdir(parents=True, exist_ok=True)
        safe_wp_id = self.waypoint.id.lower().replace("-", "")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        receipt_path = receipts_dir / f"{safe_wp_id}-{timestamp}.json"
        receipt.save(receipt_path)

        logger.info("Receipt saved: %s", receipt_path)

        # Quick check: if any commands failed, receipt is invalid
        if not receipt.is_valid():
            failed = receipt.failed_items()
            failed_names = ", ".join(item.item for item in failed)
            logger.warning("Validation commands failed: %s", failed_names)
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), False, f"Failed: {failed_names}"
            )
            return False

        # LLM verification: ask model to review the evidence
        self._report_progress(
            self.max_iterations,
            self.max_iterations,
            "finalizing",
            "Verifying receipt with LLM...",
        )

        verification_prompt = self._build_verification_prompt(receipt_path)
        verification_output = ""

        try:
            async for chunk in agent_query(
                prompt=verification_prompt,
                system_prompt="Verify the checklist receipt. Output your verdict.",
                allowed_tools=[],  # No tools needed for verification
                cwd=str(project_path),
                metrics_collector=self.metrics_collector,
                phase="fly",
                waypoint_id=self.waypoint.id,
            ):
                if isinstance(chunk, StreamChunk):
                    verification_output += chunk.text
        except Exception as e:
            logger.error("Error during receipt verification: %s", e)
            self._log_writer.log_error(0, f"Verification error: {e}")
            # Fall back to format-only validation
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), True, "LLM verification skipped"
            )
            return True  # Trust the evidence if LLM verification fails

        # Log verification output
        if verification_output:
            self._log_writer.log_finalize_output(verification_output)

        # Parse verdict
        verdict_match = re.search(
            r'<receipt-verdict status="(valid|invalid)">(.*?)</receipt-verdict>',
            verification_output,
            re.DOTALL,
        )

        if verdict_match:
            status = verdict_match.group(1)
            reasoning = verdict_match.group(2).strip()
            is_valid = status == "valid"

            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), is_valid, reasoning
            )

            if is_valid:
                logger.info("Receipt verified: %s", reasoning)
                return True
            else:
                logger.warning("Receipt rejected: %s", reasoning)
                return False
        else:
            # No verdict found, fall back to format validation
            logger.warning("No verdict marker in LLM response, using format validation")
            self._log_writer.log_finalize_end()
            self._log_writer.log_receipt_validated(
                str(receipt_path), True, "LLM verdict not found, using format check"
            )
            return True

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
