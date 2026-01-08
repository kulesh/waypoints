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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from waypoints.fly.execution_log import ExecutionLogWriter
from waypoints.git.config import Checklist
from waypoints.llm.client import StreamChunk, StreamComplete, agent_query
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint

logger = logging.getLogger(__name__)

# Max iterations before giving up
MAX_ITERATIONS = 10


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


ProgressCallback = Callable[[ExecutionContext], None]


def _build_prompt(
    waypoint: Waypoint,
    spec: str,
    project_path: Path,
    checklist: Checklist,
) -> str:
    """Build the execution prompt for a waypoint."""
    criteria_list = "\n".join(f"- [ ] {c}" for c in waypoint.acceptance_criteria)

    checklist_items = "\n".join(f"- {item}" for item in checklist.items)
    # Normalize waypoint ID for receipt filename
    safe_wp_id = waypoint.id.lower().replace("-", "")

    return f"""## Current Waypoint: {waypoint.id}
{waypoint.title}

## Objective
{waypoint.objective}

## Acceptance Criteria (must all pass)
{criteria_list}

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

**IMPORTANT RULES:**
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
    ) -> None:
        self.project = project
        self.waypoint = waypoint
        self.spec = spec
        self.on_progress = on_progress
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
        project_path = Path.cwd()  # Assume we're in the project directory

        # Load checklist from project (creates default if not exists)
        checklist = Checklist.load(self.project.get_path())

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

        while iteration < MAX_ITERATIONS:
            if self._cancelled:
                logger.info("Execution cancelled")
                self._log_writer.log_completion(ExecutionResult.CANCELLED.value)
                return ExecutionResult.CANCELLED

            iteration += 1
            logger.info("Iteration %d/%d", iteration, MAX_ITERATIONS)

            self._report_progress(
                iteration, MAX_ITERATIONS, "executing", f"Iteration {iteration}"
            )

            # Log iteration start
            iter_prompt = prompt if iteration == 1 else "Continue implementing."
            self._log_writer.log_iteration_start(iteration, iter_prompt)

            # Run agent query with file and bash tools
            iteration_output = ""
            iteration_cost: float | None = None
            try:
                async for chunk in agent_query(
                    prompt=iter_prompt,
                    system_prompt=self._get_system_prompt(),
                    allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                    cwd=str(project_path),
                ):
                    if isinstance(chunk, StreamChunk):
                        iteration_output += chunk.text
                        full_output += chunk.text

                        # Check for completion marker
                        if completion_marker in full_output:
                            logger.info("Completion marker found!")
                            self.steps.append(
                                ExecutionStep(
                                    iteration=iteration,
                                    action="complete",
                                    output=iteration_output,
                                )
                            )
                            # Log completion marker found
                            self._log_writer.log_output(iteration, iteration_output)
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

                        # Report streaming progress
                        self._report_progress(
                            iteration, MAX_ITERATIONS, "streaming", chunk.text
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
                self._report_progress(iteration, MAX_ITERATIONS, "error", f"Error: {e}")
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
                return ExecutionResult.FAILED

            # Log iteration output and end
            self._log_writer.log_output(iteration, iteration_output)
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
                self._log_writer.log_completion(
                    ExecutionResult.INTERVENTION_NEEDED.value
                )
                return ExecutionResult.INTERVENTION_NEEDED

        # Max iterations reached
        logger.warning("Max iterations (%d) reached without completion", MAX_ITERATIONS)
        self._log_writer.log_completion(ExecutionResult.MAX_ITERATIONS.value)
        return ExecutionResult.MAX_ITERATIONS

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the agent."""
        return """You are implementing a software waypoint as part of a larger project.
You have access to file and bash tools to read, write, and execute code.

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
    ) -> None:
        """Report progress to callback if set."""
        if self.on_progress:
            ctx = ExecutionContext(
                waypoint=self.waypoint,
                iteration=iteration,
                total_iterations=total,
                step=step,
                output=output,
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

    async def _finalize_and_verify_receipt(self, project_path: Path) -> bool:
        """Run finalize step to ensure receipt is produced.

        This is Step 2 of the model guidance - after completion marker,
        we remind the model about the receipt and verify it exists.

        Returns True if receipt is valid, False otherwise.
        """
        from waypoints.git.receipt import ReceiptValidator

        validator = ReceiptValidator()
        receipt_path = validator.find_latest_receipt(
            self.project.get_path(), self.waypoint.id
        )

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
            MAX_ITERATIONS, MAX_ITERATIONS, "finalizing", "Verifying checklist..."
        )

        try:
            async for chunk in agent_query(
                prompt=finalize_prompt,
                system_prompt="Finalize waypoint. Produce the checklist receipt.",
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                cwd=str(project_path),
            ):
                if isinstance(chunk, StreamChunk):
                    self._report_progress(
                        MAX_ITERATIONS, MAX_ITERATIONS, "finalizing", chunk.text
                    )
        except Exception as e:
            logger.error("Error during finalize: %s", e)
            return False

        # Check for receipt again
        receipt_path = validator.find_latest_receipt(
            self.project.get_path(), self.waypoint.id
        )
        if receipt_path:
            result = validator.validate(receipt_path)
            if result.valid:
                logger.info("Receipt created and validated: %s", receipt_path)
                return True
            else:
                logger.warning("Receipt invalid after finalize: %s", result.message)
                return False

        logger.warning("No receipt found after finalize prompt")
        return False


async def execute_waypoint(
    project: Project,
    waypoint: Waypoint,
    spec: str,
    on_progress: ProgressCallback | None = None,
) -> ExecutionResult:
    """Convenience function to execute a single waypoint.

    Args:
        project: The project containing the waypoint
        waypoint: The waypoint to execute
        spec: The product specification for context
        on_progress: Optional callback for progress updates

    Returns:
        ExecutionResult indicating success, failure, or other outcomes
    """
    executor = WaypointExecutor(project, waypoint, spec, on_progress)
    return await executor.execute()
