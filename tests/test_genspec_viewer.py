from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from waypoints.genspec.exporter import export_bundle, export_to_file
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
from waypoints.genspec.viewer import ViewOptions, load_genspec, render_view


def _build_spec() -> GenerativeSpec:
    created_at = datetime(2026, 1, 19, 23, 52, 10)
    spec = GenerativeSpec(
        version="1.0",
        waypoints_version="0.1.0",
        source_project="viewer-project",
        created_at=created_at,
        model="claude-sonnet-4",
        model_version="2024-12-01",
        initial_idea="Viewer idea",
    )
    spec.steps.append(
        GenerativeStep(
            step_id="step-001",
            phase=Phase.SHAPE_QA,
            timestamp=created_at,
            input=StepInput(user_prompt="What is this?"),
            output=StepOutput(content="A viewer", output_type=OutputType.TEXT),
        )
    )
    spec.artifacts.append(
        Artifact(
            artifact_type=ArtifactType.IDEA_BRIEF,
            content="# Brief\n",
            file_path="docs/idea-brief.md",
            timestamp=created_at,
        )
    )
    return spec


def test_viewer_loads_jsonl(tmp_path: Path) -> None:
    spec = _build_spec()
    path = tmp_path / "spec.genspec.jsonl"
    export_to_file(spec, path)

    loaded, metadata, checksums = load_genspec(path)

    assert loaded.source_project == spec.source_project
    assert metadata is None
    assert checksums is None


def test_viewer_loads_bundle(tmp_path: Path) -> None:
    spec = _build_spec()
    path = tmp_path / "spec.genspec.zip"
    export_bundle(spec, path)

    loaded, metadata, checksums = load_genspec(path)

    assert loaded.source_project == spec.source_project
    assert metadata is not None
    assert metadata.schema == "genspec-bundle"
    assert checksums is not None
    assert checksums.algorithm == "sha256"


def test_viewer_render_includes_sections() -> None:
    spec = _build_spec()
    output = render_view(spec, None, None, ViewOptions())

    assert "GenSpec View" in output
    assert "Artifacts" in output
    assert "Steps" in output


def test_cli_view(tmp_path: Path, capsys) -> None:
    from waypoints.main import cmd_view

    spec = _build_spec()
    path = tmp_path / "spec.genspec.jsonl"
    export_to_file(spec, path)

    args = argparse.Namespace(
        path=path,
        steps_limit=50,
        no_steps=False,
        no_preview=True,
        preview_lines=8,
    )

    assert cmd_view(args) == 0
    captured = capsys.readouterr()
    assert "GenSpec View" in captured.out
