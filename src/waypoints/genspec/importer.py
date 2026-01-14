"""Import a Generative Specification into a new project.

Reads a genspec.jsonl file and creates a new waypoints project
with the artifacts and dialogue history from the specification.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from waypoints.config.paths import get_paths
from waypoints.genspec.spec import (
    Artifact,
    ArtifactType,
    GenerativeSpec,
    GenerativeStep,
    UserDecision,
)
from waypoints.models.flight_plan import FlightPlanWriter
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

    spec: GenerativeSpec | None = None
    steps: list[GenerativeStep] = []
    decisions: list[UserDecision] = []
    artifacts: list[Artifact] = []

    with open(path) as f:
        for line_num, line in enumerate(f):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {line_num + 1}: {e}") from e

            # First line is header
            if line_num == 0:
                if data.get("_schema") != "genspec":
                    raise ValueError(
                        f"Invalid schema: expected 'genspec', "
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
    - Valid phase sequences
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


def create_project_from_spec(
    spec: GenerativeSpec,
    name: str,
    replay_mode: bool = True,
) -> Project:
    """Create a new project from a generative specification.

    In replay mode, uses cached outputs from the spec.
    In regenerate mode, the caller must execute steps separately.

    Args:
        spec: The specification to import
        name: Name for the new project
        replay_mode: If True, use cached outputs; if False, prepare for regeneration

    Returns:
        Created Project instance

    Raises:
        ValueError: If spec validation fails
    """
    # Validate spec
    validation = validate_spec(spec)
    if validation.has_errors:
        raise ValueError(f"Invalid spec: {', '.join(validation.errors)}")

    for warning in validation.warnings:
        logger.warning("Spec validation warning: %s", warning)

    # Create project
    paths = get_paths()
    slug = _slugify(name)

    # Ensure unique slug
    project_path = paths.projects_dir / slug
    if project_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        slug = f"{slug}-{timestamp}"
        project_path = paths.projects_dir / slug

    logger.info("Creating project: %s at %s", name, project_path)

    # Create project directory structure
    project_path.mkdir(parents=True, exist_ok=True)
    (project_path / "sessions").mkdir(exist_ok=True)
    (project_path / "docs").mkdir(exist_ok=True)

    # Create project metadata
    project = Project(
        name=name,
        slug=slug,
        created_at=datetime.now(),
        initial_idea=spec.initial_idea,
    )

    if replay_mode:
        # In replay mode, restore artifacts from spec
        _restore_artifacts(project, spec)

    # Save project
    project.save()

    logger.info("Project created: %s", project.slug)
    return project


def _restore_artifacts(project: Project, spec: GenerativeSpec) -> None:
    """Restore artifacts from spec to project directory."""
    docs_path = project.get_docs_path()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

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
                    dependencies=wp.get("dependencies", []),
                    status=WaypointStatus.PENDING,  # Reset status for new project
                    created_at=datetime.now(),
                )
                for wp in waypoints_data
            ]

            # Write flight plan
            writer = FlightPlanWriter(project)
            for wp in waypoints:
                writer.add_waypoint(wp)

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
