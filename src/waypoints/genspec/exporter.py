"""Export a waypoints project to a Generative Specification.

Collects all prompts, dialogues, and artifacts from a project
and bundles them into a structured genspec.jsonl file.
"""

import hashlib
import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from waypoints.fly.execution_log import ExecutionLogReader
from waypoints.genspec.spec import (
    Artifact,
    ArtifactType,
    BundleChecksums,
    BundleFile,
    BundleFileType,
    BundleMetadata,
    GenerativeSpec,
    GenerativeStep,
    OutputType,
    Phase,
    StepInput,
    StepMetadata,
    StepOutput,
)
from waypoints.models.dialogue import DialogueHistory, MessageRole
from waypoints.models.flight_plan import FlightPlanReader
from waypoints.models.session import SessionReader

if TYPE_CHECKING:
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)

# Bundle format constants
BUNDLE_SCHEMA = "genspec-bundle"
BUNDLE_VERSION = "1.0"
BUNDLE_ARTIFACT_PATHS = {
    ArtifactType.IDEA_BRIEF: "artifacts/idea-brief.md",
    ArtifactType.PRODUCT_SPEC: "artifacts/product-spec.md",
    ArtifactType.FLIGHT_PLAN: "artifacts/flight-plan.json",
}
_ZIP_EPOCH = (2020, 1, 1, 0, 0, 0)

# Mapping from session phase names to genspec Phase enum
PHASE_MAP = {
    "ideation": Phase.SHAPE_QA,
    "ideation-qa": Phase.SHAPE_QA,
    "idea-brief": Phase.SHAPE_BRIEF,
    "product-spec": Phase.SHAPE_SPEC,
    "chart": Phase.CHART,
}

# Known prompts by phase - these are the prompts used in the TUI screens
# We store them here so we can include them in the exported spec
KNOWN_PROMPTS = {
    Phase.SHAPE_QA: {
        "system_prompt": """\
You are a product design assistant helping crystallize an idea through dialogue.

Your role is to ask ONE clarifying question at a time to help the user refine
their idea. After each answer, briefly acknowledge what you learned, then ask
the next most important question.

Focus on understanding:
1. The core problem being solved and why it matters
2. Who the target users are and their pain points
3. Key features and capabilities needed
4. Technical constraints or preferences
5. What success looks like

Guidelines:
- Ask only ONE question per response
- Keep questions focused and specific
- Build on previous answers
- Be curious and dig deeper when answers are vague
- Don't summarize or conclude - the user will tell you when they're done

The user will press Ctrl+D when they feel the idea is sufficiently refined.
Until then, keep asking questions to deepen understanding."""
    },
    Phase.SHAPE_BRIEF: {
        "system_prompt": (
            "You are a technical writer creating concise product documentation."
        ),
        "prompt_template": """\
Based on the ideation conversation below, generate a concise Idea Brief document.

The brief should be in Markdown format and include:

# Idea Brief: [Catchy Title]

## Problem Statement
What problem are we solving and why does it matter?

## Target Users
Who are the primary users and what are their pain points?

## Proposed Solution
High-level description of what we're building.

## Key Features
- Bullet points of core capabilities

## Success Criteria
How will we know if this succeeds?

## Open Questions
Any unresolved items that need further exploration.

---

Keep it concise (under 500 words). Focus on clarity over completeness.
The goal is to capture the essence of the idea so others can quickly understand it.

Here is the ideation conversation:

{conversation}

Generate the Idea Brief now:""",
    },
    Phase.SHAPE_SPEC: {
        "system_prompt": (
            "You are a senior product manager creating detailed "
            "product specifications. Be thorough but practical."
        ),
        "prompt_template": """\
Based on the Idea Brief below, generate a comprehensive Product Specification.

The specification should be detailed enough for engineers and product managers
to understand exactly what needs to be built. Use Markdown format.

# Product Specification: [Product Name]

## 1. Executive Summary
Brief overview of the product and its value proposition.

## 2. Problem Statement
### 2.1 Current Pain Points
### 2.2 Impact of the Problem
### 2.3 Why Now?

## 3. Target Users
### 3.1 Primary Persona
### 3.2 Secondary Personas
### 3.3 User Journey

## 4. Product Overview
### 4.1 Vision Statement
### 4.2 Core Value Proposition
### 4.3 Key Differentiators

## 5. Features & Requirements
### 5.1 MVP Features (Must Have)
### 5.2 Phase 2 Features (Should Have)
### 5.3 Future Considerations (Nice to Have)

## 6. Technical Considerations
### 6.1 Architecture Overview
### 6.2 Technology Stack Recommendations
### 6.3 Integration Requirements
### 6.4 Security & Privacy

## 7. Success Metrics
### 7.1 Key Performance Indicators
### 7.2 Success Criteria for MVP

## 8. Risks & Mitigations
### 8.1 Technical Risks
### 8.2 Market Risks
### 8.3 Mitigation Strategies

## 9. FAQ
Common questions and answers for the development team.

## 10. Appendix
### 10.1 Glossary
### 10.2 References

---

Here is the Idea Brief to expand:

{brief}

Generate the complete Product Specification now:""",
    },
    Phase.CHART: {
        "system_prompt": (
            "You are a technical project planner. Create clear, testable "
            "waypoints for software development. Output valid JSON only."
        ),
        "prompt_template": """\
Based on the Product Specification below, generate a flight plan of waypoints
for building this product incrementally.

Each waypoint should:
1. Be independently testable
2. Have clear acceptance criteria
3. Be appropriately sized (1-3 hours of focused work for single-hop)
4. Use parent_id for multi-hop waypoints (epics that contain sub-tasks)

Output as a JSON array of waypoints. Each waypoint has:
- id: String like "WP-001" (use "WP-001a", "WP-001b" for children)
- title: Brief descriptive title
- objective: What this waypoint accomplishes
- acceptance_criteria: Array of testable criteria
- parent_id: ID of parent waypoint (null for top-level)
- dependencies: Array of waypoint IDs this depends on

Generate 8-15 waypoints for MVP scope. Group related work into epics where appropriate.

Output ONLY the JSON array, no markdown code blocks or other text.

Product Specification:
{spec}

Generate the waypoints JSON now:""",
    },
    Phase.FLY: {
        "system_prompt": """\
You are implementing a software waypoint as part of a larger project.
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

When complete, output the completion marker specified in the instructions.""",
    },
}


def export_project(project: "Project") -> GenerativeSpec:
    """Export a project to a GenerativeSpec.

    Args:
        project: The project to export

    Returns:
        GenerativeSpec containing all steps and artifacts
    """
    logger.info("Exporting project: %s", project.slug)

    # Get waypoints version from package
    try:
        from waypoints import __version__

        waypoints_version = __version__
    except ImportError:
        waypoints_version = "unknown"

    # Create the spec
    spec = GenerativeSpec(
        version="1.0",
        waypoints_version=waypoints_version,
        source_project=project.slug,
        created_at=datetime.now(UTC),
        initial_idea=project.initial_idea or "",
    )

    # Add SPARK step for initial idea (no LLM call, just user input)
    step_counter = 0
    if project.initial_idea:
        step_counter = 1
        spark_step = GenerativeStep(
            step_id="step-001",
            phase=Phase.SPARK,
            timestamp=project.created_at or datetime.now(UTC),
            input=StepInput(
                system_prompt=None,
                user_prompt=project.initial_idea,
            ),
            output=StepOutput(content="", output_type=OutputType.TEXT),
        )
        spec.steps.append(spark_step)

    # Collect steps from session files (SHAPE through CHART phases)
    step_counter = _collect_session_steps(project, spec, step_counter)

    # Collect FLY phase steps from execution logs
    _collect_fly_steps(project, spec, step_counter)

    # Collect artifacts
    _collect_artifacts(project, spec)

    # Get model info from any step
    for step in spec.steps:
        if step.metadata.model:
            spec.model = step.metadata.model
            break

    logger.info(
        "Exported %d steps, %d artifacts",
        len(spec.steps),
        len(spec.artifacts),
    )

    return spec


def export_bundle(spec: GenerativeSpec, path: Path) -> None:
    """Export a GenerativeSpec to a bundle zip.

    Args:
        spec: The spec to export
        path: Output bundle path
    """
    logger.info("Writing genspec bundle to %s", path)
    files = _build_bundle_files(spec)
    _write_bundle_zip(path, files)


def _build_bundle_files(spec: GenerativeSpec) -> dict[str, bytes]:
    """Build bundle files as a path -> bytes mapping."""
    files: dict[str, bytes] = {}
    bundle_files: list[BundleFile] = []

    files["genspec.jsonl"] = _serialize_spec(spec).encode("utf-8")
    bundle_files.append(
        BundleFile(path="genspec.jsonl", file_type=BundleFileType.GENSPEC)
    )

    for artifact in spec.artifacts:
        artifact_path = BUNDLE_ARTIFACT_PATHS.get(artifact.artifact_type)
        if not artifact_path:
            continue
        files[artifact_path] = artifact.content.encode("utf-8")
        bundle_files.append(
            BundleFile(
                path=artifact_path,
                file_type=BundleFileType.ARTIFACT,
                artifact_type=artifact.artifact_type,
            )
        )

    bundle_files.append(
        BundleFile(path="metadata.json", file_type=BundleFileType.METADATA)
    )
    bundle_files.append(
        BundleFile(path="checksums.json", file_type=BundleFileType.CHECKSUMS)
    )

    bundle_files_sorted = sorted(bundle_files, key=lambda entry: entry.path)
    metadata = BundleMetadata(
        schema=BUNDLE_SCHEMA,
        version=BUNDLE_VERSION,
        waypoints_version=spec.waypoints_version,
        source_project=spec.source_project,
        created_at=spec.created_at,
        model=spec.model,
        model_version=spec.model_version,
        initial_idea=spec.initial_idea or None,
        files=bundle_files_sorted,
    )
    files["metadata.json"] = _serialize_json(metadata.to_dict())

    checksums = BundleChecksums(
        algorithm="sha256",
        files=_calculate_checksums(files),
    )
    files["checksums.json"] = _serialize_json(checksums.to_dict())

    return files


def _serialize_spec(spec: GenerativeSpec) -> str:
    """Serialize a GenerativeSpec to JSONL."""
    lines = [json.dumps(spec.to_header_dict())]

    for step in spec.steps:
        lines.append(json.dumps(step.to_dict()))

    for decision in spec.decisions:
        lines.append(json.dumps(decision.to_dict()))

    for artifact in spec.artifacts:
        lines.append(json.dumps(artifact.to_dict()))

    return "\n".join(lines) + "\n"


def _serialize_json(payload: dict[str, object]) -> bytes:
    """Serialize JSON payload with stable formatting."""
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _calculate_checksums(files: dict[str, bytes]) -> dict[str, str]:
    """Compute sha256 checksums for each bundle file (excluding checksums.json)."""
    checksums: dict[str, str] = {}
    for file_path in sorted(files):
        if file_path == "checksums.json":
            continue
        checksums[file_path] = hashlib.sha256(files[file_path]).hexdigest()
    return checksums


def _write_bundle_zip(path: Path, files: dict[str, bytes]) -> None:
    """Write bundle zip deterministically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for file_path in sorted(files):
            info = zipfile.ZipInfo(file_path)
            info.date_time = _ZIP_EPOCH
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[file_path])


def _collect_session_steps(
    project: "Project", spec: GenerativeSpec, start_counter: int = 0
) -> int:
    """Collect generative steps from session files.

    Args:
        project: The project to collect from
        spec: The spec to add steps to
        start_counter: Starting step counter (default 0)

    Returns:
        The step counter after collecting all session steps.
    """
    sessions_path = project.get_sessions_path()
    if not sessions_path.exists():
        logger.warning("No sessions directory found")
        return start_counter

    # Find all session files (sorted by time)
    session_files = sorted(sessions_path.glob("*.jsonl"))
    step_counter = start_counter

    for session_file in session_files:
        # Skip fly/ subdirectory files
        if session_file.parent.name == "fly":
            continue

        try:
            history = SessionReader.load(session_file)
            phase_name = history.phase or _infer_phase_from_filename(session_file.name)
            phase = PHASE_MAP.get(phase_name)

            if not phase:
                logger.debug("Skipping unknown phase: %s", phase_name)
                continue

            # Get the known prompt for this phase
            known = KNOWN_PROMPTS.get(phase, {})
            system_prompt = known.get("system_prompt", "")

            # For QA phases, we create steps for each Q&A pair
            if phase == Phase.SHAPE_QA:
                _collect_qa_steps(history, phase, system_prompt, spec, step_counter)
                assistant_count = len(
                    [m for m in history.messages if m.role == MessageRole.ASSISTANT]
                )
                step_counter += assistant_count
            else:
                # For generation phases, create a single step
                step_counter += 1
                step = _create_generation_step(
                    step_id=f"step-{step_counter:03d}",
                    phase=phase,
                    history=history,
                    system_prompt=system_prompt,
                    session_file=session_file,
                )
                if step:
                    spec.steps.append(step)

        except Exception as e:
            logger.warning("Error processing session %s: %s", session_file, e)

    return step_counter


def _collect_qa_steps(
    history: DialogueHistory,
    phase: Phase,
    system_prompt: str,
    spec: GenerativeSpec,
    start_counter: int,
) -> None:
    """Collect Q&A conversation steps."""
    messages = history.messages
    step_num = start_counter

    # Group messages into user-assistant pairs
    for i, msg in enumerate(messages):
        if msg.role == MessageRole.USER:
            # Find the next assistant message
            assistant_content = ""
            assistant_timestamp = msg.timestamp
            for j in range(i + 1, len(messages)):
                if messages[j].role == MessageRole.ASSISTANT:
                    assistant_content = messages[j].content
                    assistant_timestamp = messages[j].timestamp
                    break

            if assistant_content:
                step_num += 1
                step = GenerativeStep(
                    step_id=f"step-{step_num:03d}",
                    phase=phase,
                    timestamp=assistant_timestamp,
                    input=StepInput(
                        system_prompt=system_prompt,
                        user_prompt=msg.content,
                        messages=[
                            {"role": m.role.value, "content": m.content}
                            for m in messages[: i + 1]
                        ],
                    ),
                    output=StepOutput(
                        content=assistant_content,
                        output_type=OutputType.TEXT,
                    ),
                )
                spec.steps.append(step)


def _create_generation_step(
    step_id: str,
    phase: Phase,
    history: DialogueHistory,
    system_prompt: str,
    session_file: Path,
) -> GenerativeStep | None:
    """Create a generation step from a session."""
    messages = history.messages
    if not messages:
        return None

    # Find the last assistant message as the output
    output_content = ""
    output_timestamp = datetime.now(UTC)
    for msg in reversed(messages):
        if msg.role == MessageRole.ASSISTANT:
            output_content = msg.content
            output_timestamp = msg.timestamp
            break

    if not output_content:
        return None

    # Find the user prompt that triggered generation
    user_prompt = ""
    for msg in messages:
        if msg.role == MessageRole.USER:
            user_prompt = msg.content
            break

    # Determine output type
    output_type = OutputType.TEXT
    if output_content.strip().startswith("{") or output_content.strip().startswith("["):
        output_type = OutputType.JSON
    elif output_content.strip().startswith("#"):
        output_type = OutputType.MARKDOWN

    return GenerativeStep(
        step_id=step_id,
        phase=phase,
        timestamp=output_timestamp,
        input=StepInput(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            messages=[{"role": m.role.value, "content": m.content} for m in messages],
        ),
        output=StepOutput(
            content=output_content,
            output_type=output_type,
        ),
    )


def _infer_phase_from_filename(filename: str) -> str:
    """Infer phase name from session filename."""
    # Format: {phase}-{timestamp}.jsonl
    parts = filename.rsplit("-", 2)
    if len(parts) >= 2:
        return parts[0]
    return ""


def _collect_artifacts(project: "Project", spec: GenerativeSpec) -> None:
    """Collect generated artifacts (brief, spec, flight plan)."""
    docs_path = project.get_docs_path()

    # Collect idea brief
    brief_files = sorted(docs_path.glob("idea-brief-*.md"), reverse=True)
    if brief_files:
        content = brief_files[0].read_text()
        spec.artifacts.append(
            Artifact(
                artifact_type=ArtifactType.IDEA_BRIEF,
                content=content,
                file_path=str(brief_files[0].relative_to(project.get_path())),
            )
        )

    # Collect product spec
    spec_files = sorted(docs_path.glob("product-spec-*.md"), reverse=True)
    if spec_files:
        content = spec_files[0].read_text()
        spec.artifacts.append(
            Artifact(
                artifact_type=ArtifactType.PRODUCT_SPEC,
                content=content,
                file_path=str(spec_files[0].relative_to(project.get_path())),
            )
        )

    # Collect flight plan
    flight_plan = FlightPlanReader.load(project)
    if flight_plan and flight_plan.waypoints:
        # Serialize waypoints to JSON
        waypoints_json = json.dumps(
            [
                {
                    "id": wp.id,
                    "title": wp.title,
                    "objective": wp.objective,
                    "acceptance_criteria": wp.acceptance_criteria,
                    "parent_id": wp.parent_id,
                    "debug_of": wp.debug_of,
                    "resolution_notes": wp.resolution_notes,
                    "dependencies": wp.dependencies,
                    "spec_context_summary": wp.spec_context_summary,
                    "spec_section_refs": wp.spec_section_refs,
                    "spec_context_hash": wp.spec_context_hash,
                    "status": wp.status.value,
                }
                for wp in flight_plan.waypoints
            ],
            indent=2,
        )
        spec.artifacts.append(
            Artifact(
                artifact_type=ArtifactType.FLIGHT_PLAN,
                content=waypoints_json,
                file_path="flight-plan.jsonl",
            )
        )


def _collect_fly_steps(
    project: "Project", spec: GenerativeSpec, step_counter: int
) -> int:
    """Collect FLY phase steps from execution logs.

    Captures all iteration prompts for each waypoint execution.

    Args:
        project: The project to collect from
        spec: The spec to add steps to
        step_counter: Current step counter

    Returns:
        Updated step counter
    """
    fly_dir = project.get_sessions_path() / "fly"
    if not fly_dir.exists():
        return step_counter

    # Get the FLY system prompt template
    known = KNOWN_PROMPTS.get(Phase.FLY, {})
    system_prompt = known.get("system_prompt", "")

    for log_file in sorted(fly_dir.glob("*.jsonl")):
        try:
            log = ExecutionLogReader.load(log_file)
            last_iteration_reason = "initial"

            # Collect ALL iteration_start entries
            for entry in log.entries:
                entry_type = entry.metadata.get("type")

                # Track reason from previous events
                if entry_type == "error":
                    last_iteration_reason = "error"
                elif entry_type == "intervention_needed":
                    last_iteration_reason = entry.metadata.get(
                        "intervention_type", "intervention"
                    )

                if entry_type == "iteration_start":
                    iteration = entry.metadata.get("iteration", 1)
                    step_counter += 1

                    # Calculate per-iteration cost estimate
                    total_iterations = sum(
                        1
                        for e in log.entries
                        if e.metadata.get("type") == "iteration_start"
                    )
                    per_iter_cost = (
                        log.total_cost_usd / total_iterations
                        if total_iterations > 0
                        else 0.0
                    )

                    step = GenerativeStep(
                        step_id=f"step-{step_counter:03d}",
                        phase=Phase.FLY,
                        timestamp=entry.timestamp,
                        input=StepInput(
                            system_prompt=system_prompt,
                            user_prompt=entry.metadata.get("prompt", ""),
                            context={
                                "waypoint_id": log.waypoint_id,
                                "waypoint_title": log.waypoint_title,
                                "iteration": iteration,
                                "iteration_reason": last_iteration_reason,
                            },
                        ),
                        output=StepOutput(content="", output_type=OutputType.TEXT),
                        metadata=StepMetadata(cost_usd=per_iter_cost),
                    )
                    spec.steps.append(step)
                    last_iteration_reason = "continue"

        except Exception as e:
            logger.warning("Error processing fly log %s: %s", log_file, e)

    return step_counter


def export_to_file(spec: GenerativeSpec, path: Path) -> None:
    """Export a GenerativeSpec to a JSONL file.

    Args:
        spec: The spec to export
        path: Path to write the file
    """
    logger.info("Writing genspec to %s", path)

    path.write_text(_serialize_spec(spec), encoding="utf-8")

    total_lines = 1 + len(spec.steps) + len(spec.decisions) + len(spec.artifacts)
    logger.info("Wrote %d lines to genspec", total_lines)
