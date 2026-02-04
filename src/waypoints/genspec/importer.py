"""Import a Generative Specification into a new project.

Reads a genspec.jsonl file and creates a new waypoints project
with the artifacts and dialogue history from the specification.
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waypoints.config.project_root import get_projects_root
from waypoints.genspec.spec import (
    Artifact,
    ArtifactType,
    GenerativeSpec,
    GenerativeStep,
    UserDecision,
)
from waypoints.models.flight_plan import FlightPlan, FlightPlanWriter
from waypoints.models.journey import Journey, JourneyState
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint, WaypointStatus

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validating a generative specification."""

    valid: bool
    errors: list[str]
    warnings: list[str]

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


def import_from_file(path: Path) -> GenerativeSpec:
    """Import a GenerativeSpec from a JSONL file.

    Args:
        path: Path to the genspec.jsonl file

    Returns:
        Parsed GenerativeSpec

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is invalid
    """
    if not path.exists():
        raise FileNotFoundError(f"Genspec file not found: {path}")

    logger.info("Importing genspec from %s", path)
    with open(path, encoding="utf-8") as handle:
        return import_from_lines(handle, source=str(path))


def import_from_lines(
    lines: Iterable[str], *, source: str | None = None
) -> GenerativeSpec:
    """Import a GenerativeSpec from JSONL lines.

    Args:
        lines: Iterable of JSONL lines
        source: Optional source description for error messages

    Returns:
        Parsed GenerativeSpec
    """
    spec: GenerativeSpec | None = None
    steps: list[GenerativeStep] = []
    decisions: list[UserDecision] = []
    artifacts: list[Artifact] = []

    for line_num, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            location = f" in {source}" if source else ""
            raise ValueError(
                f"Invalid JSON on line {line_num + 1}{location}: {exc}"
            ) from exc

        # First line is header
        if line_num == 0:
            if data.get("_schema") != "genspec":
                raise ValueError(
                    "Invalid schema: expected 'genspec', "
                    f"got '{data.get('_schema')}'"
                )
            spec = GenerativeSpec.from_header_dict(data)
            continue

        # Parse entry by type
        entry_type = data.get("type")
        if entry_type == "step":
            steps.append(GenerativeStep.from_dict(data))
        elif entry_type == "decision":
            decisions.append(UserDecision.from_dict(data))
        elif entry_type == "artifact":
            artifacts.append(Artifact.from_dict(data))
        else:
            logger.warning(
                "Unknown entry type on line %d: %s", line_num + 1, entry_type
            )

    if spec is None:
        raise ValueError("Missing header in genspec file")

    spec.steps = steps
    spec.decisions = decisions
    spec.artifacts = artifacts

    logger.info(
        "Imported spec: %d steps, %d decisions, %d artifacts",
        len(steps),
        len(decisions),
        len(artifacts),
    )

    return spec


def validate_spec(spec: GenerativeSpec) -> ValidationResult:
    """Validate a generative specification.

    Checks for:
    - Required artifacts (idea_brief, product_spec, flight_plan)
    - Valid waypoint structure (required fields, unique IDs, valid references)
    - Non-empty content

    Args:
        spec: The spec to validate

    Returns:
        ValidationResult with errors and warnings
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Check for required artifacts
    artifact_types = {a.artifact_type for a in spec.artifacts}

    if ArtifactType.IDEA_BRIEF not in artifact_types:
        warnings.append("Missing idea_brief artifact")

    if ArtifactType.PRODUCT_SPEC not in artifact_types:
        warnings.append("Missing product_spec artifact")

    if ArtifactType.FLIGHT_PLAN not in artifact_types:
        errors.append("Missing flight_plan artifact - cannot create waypoints")

    # Check for empty flight plan
    flight_plan = spec.get_artifact(ArtifactType.FLIGHT_PLAN)
    if flight_plan and not flight_plan.content.strip():
        errors.append("Empty flight_plan artifact")

    # Validate flight plan waypoints structure
    if flight_plan and flight_plan.content.strip():
        try:
            waypoints_data = json.loads(flight_plan.content)
            waypoint_ids: set[str] = set()

            # First pass: collect IDs and check required fields
            for wp in waypoints_data:
                wp_id = wp.get("id")
                if not wp_id:
                    errors.append("Waypoint missing 'id' field")
                    continue

                if not wp.get("title"):
                    errors.append(f"Waypoint {wp_id} missing 'title' field")

                if not wp.get("objective"):
                    warnings.append(f"Waypoint {wp_id} missing 'objective' field")

                # Check for duplicate IDs
                if wp_id in waypoint_ids:
                    errors.append(f"Duplicate waypoint ID: {wp_id}")
                waypoint_ids.add(wp_id)

            # Second pass: validate references
            for wp in waypoints_data:
                wp_id = wp.get("id")
                if not wp_id:
                    continue

                # Check parent reference
                parent_id = wp.get("parent_id")
                if parent_id and parent_id not in waypoint_ids:
                    errors.append(
                        f"Waypoint {wp_id} references non-existent parent: {parent_id}"
                    )

                # Check dependency references
                for dep_id in wp.get("dependencies", []):
                    if dep_id not in waypoint_ids:
                        errors.append(
                            f"Waypoint {wp_id} has invalid dependency: {dep_id}"
                        )

            # Check for circular dependencies
            if _has_circular_dependencies(waypoints_data):
                errors.append("Circular dependencies detected in waypoints")

        except json.JSONDecodeError as e:
            errors.append(f"Invalid flight plan JSON: {e}")

    # Check for steps
    if not spec.steps:
        warnings.append("No generative steps recorded")

    # Validate initial idea
    if not spec.initial_idea:
        warnings.append("No initial idea recorded")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def _has_circular_dependencies(waypoints: list[dict[str, Any]]) -> bool:
    """Check for circular dependencies using DFS.

    Args:
        waypoints: List of waypoint dictionaries

    Returns:
        True if circular dependencies exist
    """
    # Build adjacency list
    deps: dict[str, list[str]] = {}
    for wp in waypoints:
        wp_id = wp.get("id")
        if wp_id:
            deps[wp_id] = wp.get("dependencies", [])

    # Track visited and current path
    visited: set[str] = set()
    path: set[str] = set()

    def has_cycle(node: str) -> bool:
        if node in path:
            return True
        if node in visited:
            return False

        visited.add(node)
        path.add(node)

        for dep in deps.get(node, []):
            if has_cycle(dep):
                return True

        path.remove(node)
        return False

    for wp_id in deps:
        if has_cycle(wp_id):
            return True

    return False


def validate_genspec_file(path: str | Path) -> ValidationResult:
    """Quick validation of a genspec file without full import.

    Used by import modal for real-time feedback.

    Args:
        path: Path to the genspec file

    Returns:
        ValidationResult with errors and warnings
    """
    try:
        spec = import_from_file(Path(path))
        return validate_spec(spec)
    except FileNotFoundError:
        return ValidationResult(valid=False, errors=["File not found"], warnings=[])
    except ValueError as e:
        return ValidationResult(valid=False, errors=[str(e)], warnings=[])


def create_project_from_spec(
    spec_or_path: str | Path | GenerativeSpec,
    name: str | None = None,
    target_state: str = "chart:review",
    replay_mode: bool = True,
) -> Project:
    """Create a new project from a generative specification.

    Args:
        spec_or_path: Either a GenerativeSpec object or path to .genspec.jsonl file
        name: Name for the new project (defaults to spec's project name)
        target_state: Journey state to set after import:
            - "fly:ready": Ready to execute waypoints (for "Run Now" mode)
            - "chart:review": At chart review for inspection (for "Review First" mode)
        replay_mode: If True, use cached outputs; if False, prepare for regeneration

    Returns:
        Created Project instance

    Raises:
        ValueError: If spec validation fails
        FileNotFoundError: If spec file doesn't exist
    """
    # Load spec from file or use directly
    if isinstance(spec_or_path, GenerativeSpec):
        spec = spec_or_path
    else:
        spec = import_from_file(Path(spec_or_path))

    # Validate spec
    validation = validate_spec(spec)
    if validation.has_errors:
        raise ValueError(f"Invalid spec: {', '.join(validation.errors)}")

    for warning in validation.warnings:
        logger.warning("Spec validation warning: %s", warning)

    # Use spec's source project name if not provided
    project_name = name or spec.source_project or "Imported Project"

    # Create project
    slug = _slugify(project_name)
    projects_root = get_projects_root()

    # Ensure unique slug
    project_path = projects_root / slug
    if project_path.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        slug = f"{slug}-{timestamp}"
        project_path = projects_root / slug

    logger.info("Creating project: %s at %s", project_name, project_path)

    # Create project metadata
    now = datetime.now(UTC)
    project = Project(
        name=project_name,
        slug=slug,
        created_at=now,
        updated_at=now,
        initial_idea=spec.initial_idea,
    )

    # Create project directory structure using Project's path management
    # (ensures consistency with settings.project_directory)
    project._ensure_directories()

    if replay_mode:
        # In replay mode, restore artifacts from spec
        _restore_artifacts(project, spec)

    # Initialize journey with target state
    project.journey = _create_journey_at_state(slug, target_state)

    # Save project
    project.save()

    logger.info("Project created: %s at state %s", project.slug, target_state)
    return project


def _create_journey_at_state(project_slug: str, target_state: str) -> Journey:
    """Create a journey initialized to a specific state.

    For imported projects, we create a journey with history showing
    the progression through phases to reach the target state.

    Args:
        project_slug: The project's slug
        target_state: Target state ("fly:ready" or "chart:review")

    Returns:
        Journey instance at the target state
    """
    # State progression for imported projects
    # We record the history as if the phases were completed
    state_progression = [
        JourneyState.SPARK_IDLE,
        JourneyState.SPARK_ENTERING,
        JourneyState.SHAPE_QA,
        JourneyState.SHAPE_BRIEF_GENERATING,
        JourneyState.SHAPE_BRIEF_REVIEW,
        JourneyState.SHAPE_SPEC_GENERATING,
        JourneyState.SHAPE_SPEC_REVIEW,
        JourneyState.CHART_GENERATING,
        JourneyState.CHART_REVIEW,
    ]

    if target_state == "fly:ready":
        state_progression.append(JourneyState.FLY_READY)

    # Build journey by transitioning through states
    journey = Journey.new(project_slug)
    for state in state_progression[1:]:  # Skip SPARK_IDLE (initial state)
        journey = journey.transition(state)

    return journey


def _restore_artifacts(project: Project, spec: GenerativeSpec) -> None:
    """Restore artifacts from spec to project directory."""
    docs_path = project.get_docs_path()
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    # Restore idea brief
    brief = spec.get_artifact(ArtifactType.IDEA_BRIEF)
    if brief:
        brief_path = docs_path / f"idea-brief-{timestamp}.md"
        brief_path.write_text(brief.content)
        logger.info("Restored idea brief to %s", brief_path)

    # Restore product spec
    product_spec = spec.get_artifact(ArtifactType.PRODUCT_SPEC)
    if product_spec:
        spec_path = docs_path / f"product-spec-{timestamp}.md"
        spec_path.write_text(product_spec.content)
        logger.info("Restored product spec to %s", spec_path)

    # Restore flight plan
    flight_plan = spec.get_artifact(ArtifactType.FLIGHT_PLAN)
    if flight_plan:
        try:
            waypoints_data = json.loads(flight_plan.content)
            waypoints = [
                Waypoint(
                    id=wp["id"],
                    title=wp["title"],
                    objective=wp["objective"],
                    acceptance_criteria=wp.get("acceptance_criteria", []),
                    parent_id=wp.get("parent_id"),
                    debug_of=wp.get("debug_of"),
                    resolution_notes=wp.get("resolution_notes", []),
                    dependencies=wp.get("dependencies", []),
                    status=WaypointStatus.PENDING,  # Reset status for new project
                    created_at=datetime.now(UTC),
                )
                for wp in waypoints_data
            ]

            # Write flight plan
            flight_plan_obj = FlightPlan(waypoints=waypoints)
            writer = FlightPlanWriter(project)
            writer.save(flight_plan_obj)

            logger.info("Restored %d waypoints", len(waypoints))
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to restore flight plan: %s", e)
            raise ValueError(f"Invalid flight plan format: {e}") from e


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    import re

    # Convert to lowercase
    slug = name.lower()
    # Replace spaces and underscores with hyphens
    slug = re.sub(r"[\s_]+", "-", slug)
    # Remove non-alphanumeric characters (except hyphens)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    # Remove consecutive hyphens
    slug = re.sub(r"-+", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")

    return slug or "project"
