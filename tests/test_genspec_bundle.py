from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path

from waypoints.genspec.exporter import export_bundle
from waypoints.genspec.spec import (
    Artifact,
    ArtifactType,
    GenerativeSpec,
    GenerativeStep,
    OutputType,
    Phase,
    StepInput,
    StepOutput,
)
from waypoints.models.flight_plan import FlightPlan, FlightPlanWriter
from waypoints.models.project import Project
from waypoints.models.waypoint import Waypoint


def _build_spec() -> GenerativeSpec:
    created_at = datetime(2026, 1, 19, 23, 52, 10)
    spec = GenerativeSpec(
        version="1.0",
        waypoints_version="0.1.0",
        source_project="demo-project",
        created_at=created_at,
        model="claude-sonnet-4",
        model_version="2024-12-01",
        initial_idea="Demo idea",
    )
    spec.steps.append(
        GenerativeStep(
            step_id="step-001",
            phase=Phase.SHAPE_QA,
            timestamp=created_at,
            input=StepInput(user_prompt="What is this?"),
            output=StepOutput(content="A demo", output_type=OutputType.TEXT),
        )
    )
    spec.artifacts.extend(
        [
            Artifact(
                artifact_type=ArtifactType.IDEA_BRIEF,
                content="# Idea Brief\n",
                file_path="docs/idea-brief.md",
                timestamp=created_at,
            ),
            Artifact(
                artifact_type=ArtifactType.PRODUCT_SPEC,
                content="# Product Spec\n",
                file_path="docs/product-spec.md",
                timestamp=created_at,
            ),
            Artifact(
                artifact_type=ArtifactType.FLIGHT_PLAN,
                content='[{"id":"WP-001","title":"Init","objective":"Start"}]\n',
                file_path="flight-plan.json",
                timestamp=created_at,
            ),
        ]
    )
    return spec


def test_export_bundle_creates_expected_files(tmp_path: Path) -> None:
    spec = _build_spec()
    bundle_path = tmp_path / "bundle.zip"

    export_bundle(spec, bundle_path)

    with zipfile.ZipFile(bundle_path) as archive:
        names = set(archive.namelist())

    assert "genspec.jsonl" in names
    assert "metadata.json" in names
    assert "checksums.json" in names
    assert "artifacts/idea-brief.md" in names
    assert "artifacts/product-spec.md" in names
    assert "artifacts/flight-plan.json" in names


def test_export_bundle_checksums_match(tmp_path: Path) -> None:
    spec = _build_spec()
    bundle_path = tmp_path / "bundle.zip"

    export_bundle(spec, bundle_path)

    with zipfile.ZipFile(bundle_path) as archive:
        checksums = json.loads(archive.read("checksums.json").decode("utf-8"))
        checksum_map = checksums["files"]

        for file_path, expected_hash in checksum_map.items():
            content = archive.read(file_path)
            actual_hash = hashlib.sha256(content).hexdigest()
            assert actual_hash == expected_hash

        assert "checksums.json" not in checksum_map


def test_export_bundle_is_deterministic(tmp_path: Path) -> None:
    spec = _build_spec()
    bundle_a = tmp_path / "bundle-a.zip"
    bundle_b = tmp_path / "bundle-b.zip"

    export_bundle(spec, bundle_a)
    export_bundle(spec, bundle_b)

    assert bundle_a.read_bytes() == bundle_b.read_bytes()


def test_cli_export_bundle(tmp_path: Path) -> None:
    from waypoints.config.settings import settings
    from waypoints.main import cmd_export

    settings.project_directory = tmp_path
    project = Project.create("CLI Bundle", idea="Test idea")

    docs_path = project.get_docs_path()
    (docs_path / "idea-brief-20260101-000000.md").write_text(
        "# Idea Brief\n", encoding="utf-8"
    )
    (docs_path / "product-spec-20260101-000000.md").write_text(
        "# Product Spec\n", encoding="utf-8"
    )

    flight_plan = FlightPlan(
        waypoints=[
            Waypoint(
                id="WP-001",
                title="Init",
                objective="Start",
                acceptance_criteria=["Project initializes"],
            )
        ]
    )
    FlightPlanWriter(project).save(flight_plan)

    output_path = tmp_path / "bundle.zip"
    args = argparse.Namespace(
        project=project.slug,
        output=output_path,
        bundle=True,
    )

    assert cmd_export(args) == 0
    assert output_path.exists()
