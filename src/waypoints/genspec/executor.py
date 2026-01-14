"""Execute a Generative Specification to regenerate a project.

Supports three execution modes:
- REPLAY: Use cached outputs from the spec (instant)
- REGENERATE: Call LLM with stored prompts (creates new outputs)
- COMPARE: Do both and show differences
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Callable

from waypoints.genspec.importer import create_project_from_spec
from waypoints.genspec.spec import (
    ArtifactType,
    GenerativeSpec,
    GenerativeStep,
    OutputType,
    Phase,
    StepOutput,
)

if TYPE_CHECKING:
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)


class ExecutionMode(Enum):
    """Mode for executing a generative specification."""

    REPLAY = "replay"  # Use cached outputs
    REGENERATE = "regenerate"  # Call LLM fresh
    COMPARE = "compare"  # Both, then diff


@dataclass
class StepResult:
    """Result of executing a single step."""

    step_id: str
    phase: Phase
    success: bool
    output: StepOutput | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass
class ExecutionResult:
    """Result of executing a full specification."""

    mode: ExecutionMode
    project: "Project | None" = None
    step_results: list[StepResult] = field(default_factory=list)
    artifacts_created: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None and all(r.success for r in self.step_results)

    @property
    def failed_steps(self) -> list[StepResult]:
        return [r for r in self.step_results if not r.success]


# Type for progress callback
ProgressCallback = Callable[[str, int, int], None]


def execute_spec(
    spec: GenerativeSpec,
    project_name: str,
    mode: ExecutionMode = ExecutionMode.REPLAY,
    on_progress: ProgressCallback | None = None,
) -> ExecutionResult:
    """Execute a generative specification.

    Args:
        spec: The specification to execute
        project_name: Name for the created/regenerated project
        mode: Execution mode (REPLAY, REGENERATE, or COMPARE)
        on_progress: Optional callback for progress updates (message, current, total)

    Returns:
        ExecutionResult with project and step results
    """
    logger.info("Executing spec in %s mode", mode.value)

    if mode == ExecutionMode.REPLAY:
        return _execute_replay(spec, project_name, on_progress)
    elif mode == ExecutionMode.REGENERATE:
        return _execute_regenerate(spec, project_name, on_progress)
    elif mode == ExecutionMode.COMPARE:
        return _execute_compare(spec, project_name, on_progress)
    else:
        return ExecutionResult(
            mode=mode,
            error=f"Unknown execution mode: {mode}",
        )


def _execute_replay(
    spec: GenerativeSpec,
    project_name: str,
    on_progress: ProgressCallback | None = None,
) -> ExecutionResult:
    """Execute in replay mode - use cached outputs."""
    result = ExecutionResult(mode=ExecutionMode.REPLAY)
    start_time = datetime.now()

    if on_progress:
        on_progress("Creating project from cached artifacts...", 0, 1)

    try:
        # Create project with cached artifacts
        project = create_project_from_spec(spec, project_name, replay_mode=True)
        result.project = project

        # Record artifacts created
        for artifact in spec.artifacts:
            result.artifacts_created.append(artifact.artifact_type.value)

        # Mark all steps as replayed (instant success)
        for step in spec.steps:
            result.step_results.append(
                StepResult(
                    step_id=step.step_id,
                    phase=step.phase,
                    success=True,
                    output=step.output,
                )
            )

        if on_progress:
            on_progress("Project created successfully", 1, 1)

    except Exception as e:
        logger.exception("Replay execution failed: %s", e)
        result.error = str(e)
        if on_progress:
            on_progress(f"Error: {e}", 1, 1)

    result.total_duration_ms = int(
        (datetime.now() - start_time).total_seconds() * 1000
    )
    return result


def _execute_regenerate(
    spec: GenerativeSpec,
    project_name: str,
    on_progress: ProgressCallback | None = None,
) -> ExecutionResult:
    """Execute in regenerate mode - call LLM for each step."""
    result = ExecutionResult(mode=ExecutionMode.REGENERATE)
    start_time = datetime.now()

    try:
        # First create the project structure without artifacts
        project = create_project_from_spec(spec, project_name, replay_mode=False)
        result.project = project

        total_steps = len(spec.steps)

        if on_progress:
            on_progress("Regenerating from prompts...", 0, total_steps)

        # For each step, call LLM with the stored prompt
        for i, step in enumerate(spec.steps):
            if on_progress:
                on_progress(
                    f"Regenerating {step.phase.value} step {step.step_id}...",
                    i,
                    total_steps,
                )

            step_result = _regenerate_step(step, project)
            result.step_results.append(step_result)

            if step_result.success and step_result.output:
                result.total_cost_usd += 0  # Would need to track from LLM call

            if not step_result.success:
                logger.warning(
                    "Step %s failed: %s", step.step_id, step_result.error
                )

        # After regenerating steps, create artifacts from final outputs
        _create_artifacts_from_steps(result, project, spec)

        if on_progress:
            on_progress("Regeneration complete", total_steps, total_steps)

    except Exception as e:
        logger.exception("Regenerate execution failed: %s", e)
        result.error = str(e)
        if on_progress:
            on_progress(f"Error: {e}", 0, 1)

    result.total_duration_ms = int(
        (datetime.now() - start_time).total_seconds() * 1000
    )
    return result


def _regenerate_step(step: GenerativeStep, project: "Project") -> StepResult:
    """Regenerate a single step by calling the LLM.

    This is a simplified implementation that uses the stored prompts
    to call the LLM and get new outputs.
    """
    from waypoints.llm.client import ChatClient, StreamChunk, StreamComplete

    step_start = datetime.now()

    try:
        # Build messages from step input
        messages = step.input.messages or []
        if step.input.user_prompt and not messages:
            messages = [{"role": "user", "content": step.input.user_prompt}]

        if not messages:
            # No prompt to regenerate - use cached output
            return StepResult(
                step_id=step.step_id,
                phase=step.phase,
                success=True,
                output=step.output,
            )

        # Call LLM
        client = ChatClient(phase=f"regenerate-{step.phase.value}")
        content = ""

        for result in client.stream_message(
            messages=messages,
            system=step.input.system_prompt or "",
        ):
            if isinstance(result, StreamChunk):
                content += result.text
            elif isinstance(result, StreamComplete):
                pass

        # Determine output type
        output_type = OutputType.TEXT
        if content.strip().startswith(("{", "[")):
            output_type = OutputType.JSON
        elif content.strip().startswith("#"):
            output_type = OutputType.MARKDOWN

        duration_ms = int((datetime.now() - step_start).total_seconds() * 1000)

        return StepResult(
            step_id=step.step_id,
            phase=step.phase,
            success=True,
            output=StepOutput(content=content, output_type=output_type),
            duration_ms=duration_ms,
        )

    except Exception as e:
        logger.exception("Step %s failed: %s", step.step_id, e)
        duration_ms = int((datetime.now() - step_start).total_seconds() * 1000)
        return StepResult(
            step_id=step.step_id,
            phase=step.phase,
            success=False,
            error=str(e),
            duration_ms=duration_ms,
        )


def _create_artifacts_from_steps(
    result: ExecutionResult,
    project: "Project",
    spec: GenerativeSpec,
) -> None:
    """Create artifacts from regenerated step outputs."""
    docs_path = project.get_docs_path()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Find the last successful output for each artifact type
    brief_content = None
    spec_content = None

    for step_result in result.step_results:
        if not step_result.success or not step_result.output:
            continue

        if step_result.phase == Phase.SHAPE_BRIEF:
            brief_content = step_result.output.content
        elif step_result.phase == Phase.SHAPE_SPEC:
            spec_content = step_result.output.content

    # Write idea brief
    if brief_content:
        brief_path = docs_path / f"idea-brief-{timestamp}.md"
        brief_path.write_text(brief_content)
        result.artifacts_created.append("idea_brief")
        logger.info("Created idea brief: %s", brief_path)

    # Write product spec
    if spec_content:
        spec_path = docs_path / f"product-spec-{timestamp}.md"
        spec_path.write_text(spec_content)
        result.artifacts_created.append("product_spec")
        logger.info("Created product spec: %s", spec_path)

    # For flight plan, use the original (waypoints are structural)
    # In a full implementation, you might regenerate these too
    flight_plan = spec.get_artifact(ArtifactType.FLIGHT_PLAN)
    if flight_plan:
        from waypoints.genspec.importer import _restore_artifacts

        # Restore just the flight plan from original spec
        temp_spec = GenerativeSpec(
            version=spec.version,
            waypoints_version=spec.waypoints_version,
            source_project=spec.source_project,
            created_at=datetime.now(),
            artifacts=[flight_plan],
        )
        try:
            _restore_artifacts(project, temp_spec)
            result.artifacts_created.append("flight_plan")
        except Exception as e:
            logger.error("Failed to restore flight plan: %s", e)


def _execute_compare(
    spec: GenerativeSpec,
    project_name: str,
    on_progress: ProgressCallback | None = None,
) -> ExecutionResult:
    """Execute in compare mode - run both replay and regenerate, then diff."""
    result = ExecutionResult(mode=ExecutionMode.COMPARE)
    start_time = datetime.now()

    if on_progress:
        on_progress("Running comparison (replay + regenerate)...", 0, 2)

    try:
        # Run replay
        replay_result = _execute_replay(
            spec, f"{project_name}-replay", on_progress=None
        )

        if on_progress:
            on_progress("Replay complete, starting regeneration...", 1, 2)

        # Run regenerate
        regen_result = _execute_regenerate(
            spec, f"{project_name}-regen", on_progress=None
        )

        # Use the regenerated project as the result
        result.project = regen_result.project
        result.step_results = regen_result.step_results
        result.artifacts_created = regen_result.artifacts_created
        result.total_cost_usd = regen_result.total_cost_usd

        # Store comparison info in the result
        # In a full implementation, you would compute diffs here
        if replay_result.project and regen_result.project:
            logger.info(
                "Comparison complete: replay=%s, regen=%s",
                replay_result.project.slug,
                regen_result.project.slug,
            )

        if on_progress:
            on_progress("Comparison complete", 2, 2)

    except Exception as e:
        logger.exception("Compare execution failed: %s", e)
        result.error = str(e)
        if on_progress:
            on_progress(f"Error: {e}", 0, 2)

    result.total_duration_ms = int(
        (datetime.now() - start_time).total_seconds() * 1000
    )
    return result
