"""Tests for genspec module (generative specifications)."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from waypoints.genspec.spec import (
    Artifact,
    ArtifactType,
    DecisionType,
    GenerativeSpec,
    GenerativeStep,
    OutputType,
    Phase,
    StepInput,
    StepMetadata,
    StepOutput,
    UserDecision,
)


class TestStepInput:
    """Tests for StepInput dataclass."""

    def test_to_dict_minimal(self) -> None:
        """Minimal StepInput serializes to empty dict."""
        step_input = StepInput()
        assert step_input.to_dict() == {}

    def test_to_dict_full(self) -> None:
        """Full StepInput includes all fields."""
        step_input = StepInput(
            system_prompt="You are helpful",
            user_prompt="Hello",
            messages=[{"role": "user", "content": "Hi"}],
            context={"key": "value"},
        )
        result = step_input.to_dict()
        assert result["system_prompt"] == "You are helpful"
        assert result["user_prompt"] == "Hello"
        assert result["messages"] == [{"role": "user", "content": "Hi"}]
        assert result["context"] == {"key": "value"}

    def test_from_dict(self) -> None:
        """StepInput deserializes from dict."""
        data = {
            "system_prompt": "System",
            "user_prompt": "User",
            "messages": [{"role": "assistant", "content": "Response"}],
            "context": {"initial_idea": "Build something"},
        }
        step_input = StepInput.from_dict(data)
        assert step_input.system_prompt == "System"
        assert step_input.user_prompt == "User"
        assert len(step_input.messages) == 1
        assert step_input.context["initial_idea"] == "Build something"

    def test_from_dict_empty(self) -> None:
        """StepInput from empty dict uses defaults."""
        step_input = StepInput.from_dict({})
        assert step_input.system_prompt is None
        assert step_input.user_prompt == ""
        assert step_input.messages == []
        assert step_input.context == {}


class TestStepOutput:
    """Tests for StepOutput dataclass."""

    def test_to_dict(self) -> None:
        """StepOutput serializes correctly."""
        output = StepOutput(
            content="Response text",
            output_type=OutputType.MARKDOWN,
            parsed={"title": "Test"},
        )
        result = output.to_dict()
        assert result["content"] == "Response text"
        assert result["type"] == "markdown"
        assert result["parsed"] == {"title": "Test"}

    def test_to_dict_no_parsed(self) -> None:
        """StepOutput without parsed field omits it."""
        output = StepOutput(content="Text", output_type=OutputType.TEXT)
        result = output.to_dict()
        assert "parsed" not in result

    def test_from_dict(self) -> None:
        """StepOutput deserializes from dict."""
        data = {"content": "JSON data", "type": "json", "parsed": [1, 2, 3]}
        output = StepOutput.from_dict(data)
        assert output.content == "JSON data"
        assert output.output_type == OutputType.JSON
        assert output.parsed == [1, 2, 3]


class TestStepMetadata:
    """Tests for StepMetadata dataclass."""

    def test_to_dict_empty(self) -> None:
        """Empty metadata returns empty dict."""
        meta = StepMetadata()
        assert meta.to_dict() == {}

    def test_to_dict_partial(self) -> None:
        """Partial metadata only includes set fields."""
        meta = StepMetadata(tokens_in=100, cost_usd=0.01)
        result = meta.to_dict()
        assert result == {"tokens_in": 100, "cost_usd": 0.01}

    def test_from_dict(self) -> None:
        """StepMetadata deserializes correctly."""
        data = {
            "tokens_in": 500,
            "tokens_out": 200,
            "cost_usd": 0.05,
            "latency_ms": 1500,
            "model": "claude-sonnet-4",
        }
        meta = StepMetadata.from_dict(data)
        assert meta.tokens_in == 500
        assert meta.tokens_out == 200
        assert meta.cost_usd == 0.05
        assert meta.latency_ms == 1500
        assert meta.model == "claude-sonnet-4"


class TestGenerativeStep:
    """Tests for GenerativeStep dataclass."""

    def test_to_dict(self) -> None:
        """GenerativeStep serializes to JSONL format."""
        step = GenerativeStep(
            step_id="step-001",
            phase=Phase.SHAPE_QA,
            timestamp=datetime(2026, 1, 14, 10, 30, 0),
            input=StepInput(user_prompt="What is this?"),
            output=StepOutput(content="A test", output_type=OutputType.TEXT),
        )
        result = step.to_dict()
        assert result["type"] == "step"
        assert result["step_id"] == "step-001"
        assert result["phase"] == "shape_qa"
        assert result["timestamp"] == "2026-01-14T10:30:00"
        assert result["input"]["user_prompt"] == "What is this?"
        assert result["output"]["content"] == "A test"

    def test_from_dict(self) -> None:
        """GenerativeStep deserializes from dict."""
        data = {
            "type": "step",
            "step_id": "step-002",
            "phase": "chart",
            "timestamp": "2026-01-14T12:00:00",
            "input": {"system_prompt": "You are a planner"},
            "output": {"content": "[{...}]", "type": "json"},
            "metadata": {"cost_usd": 0.02},
        }
        step = GenerativeStep.from_dict(data)
        assert step.step_id == "step-002"
        assert step.phase == Phase.CHART
        assert step.input.system_prompt == "You are a planner"
        assert step.output.output_type == OutputType.JSON
        assert step.metadata.cost_usd == 0.02


class TestUserDecision:
    """Tests for UserDecision dataclass."""

    def test_to_dict_accept(self) -> None:
        """Accept decision serializes correctly."""
        decision = UserDecision(
            step_id="step-001",
            phase=Phase.CHART,
            decision=DecisionType.ACCEPT,
            timestamp=datetime(2026, 1, 14, 10, 30, 0),
        )
        result = decision.to_dict()
        assert result["type"] == "decision"
        assert result["decision"] == "accept"
        assert "edits" not in result

    def test_to_dict_edit(self) -> None:
        """Edit decision includes before/after."""
        decision = UserDecision(
            step_id="step-002",
            phase=Phase.SHAPE_BRIEF,
            decision=DecisionType.EDIT,
            edits={"before": "old text", "after": "new text"},
        )
        result = decision.to_dict()
        assert result["decision"] == "edit"
        assert result["edits"]["before"] == "old text"
        assert result["edits"]["after"] == "new text"


class TestArtifact:
    """Tests for Artifact dataclass."""

    def test_to_dict(self) -> None:
        """Artifact serializes correctly."""
        artifact = Artifact(
            artifact_type=ArtifactType.IDEA_BRIEF,
            content="# Brief\n\nContent here",
            file_path="docs/idea-brief-20260114.md",
        )
        result = artifact.to_dict()
        assert result["type"] == "artifact"
        assert result["artifact_type"] == "idea_brief"
        assert result["content"].startswith("# Brief")
        assert result["file_path"] == "docs/idea-brief-20260114.md"

    def test_from_dict(self) -> None:
        """Artifact deserializes from dict."""
        data = {
            "type": "artifact",
            "artifact_type": "product_spec",
            "content": "# Spec\n...",
            "timestamp": "2026-01-14T10:00:00",
        }
        artifact = Artifact.from_dict(data)
        assert artifact.artifact_type == ArtifactType.PRODUCT_SPEC
        assert artifact.content == "# Spec\n..."
        assert artifact.file_path is None


class TestGenerativeSpec:
    """Tests for GenerativeSpec dataclass."""

    def test_to_header_dict(self) -> None:
        """GenerativeSpec header serializes correctly."""
        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="test-project",
            created_at=datetime(2026, 1, 14, 10, 0, 0),
            model="claude-sonnet-4",
            initial_idea="Build a todo app",
        )
        result = spec.to_header_dict()
        assert result["_schema"] == "genspec"
        assert result["_version"] == "1.0"
        assert result["waypoints_version"] == "0.1.0"
        assert result["source_project"] == "test-project"
        assert result["model"] == "claude-sonnet-4"
        assert result["initial_idea"] == "Build a todo app"

    def test_from_header_dict(self) -> None:
        """GenerativeSpec creates from header dict."""
        data = {
            "_schema": "genspec",
            "_version": "1.0",
            "waypoints_version": "0.2.0",
            "source_project": "my-app",
            "created_at": "2026-01-14T12:00:00",
            "model": "claude-opus-4",
        }
        spec = GenerativeSpec.from_header_dict(data)
        assert spec.version == "1.0"
        assert spec.waypoints_version == "0.2.0"
        assert spec.source_project == "my-app"
        assert spec.model == "claude-opus-4"
        assert spec.steps == []

    def test_get_steps_by_phase(self) -> None:
        """Filter steps by phase."""
        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="test",
            created_at=datetime.now(),
        )
        spec.steps = [
            GenerativeStep(
                step_id="1",
                phase=Phase.SHAPE_QA,
                timestamp=datetime.now(),
                input=StepInput(),
                output=StepOutput(content=""),
            ),
            GenerativeStep(
                step_id="2",
                phase=Phase.CHART,
                timestamp=datetime.now(),
                input=StepInput(),
                output=StepOutput(content=""),
            ),
            GenerativeStep(
                step_id="3",
                phase=Phase.SHAPE_QA,
                timestamp=datetime.now(),
                input=StepInput(),
                output=StepOutput(content=""),
            ),
        ]
        qa_steps = spec.get_steps_by_phase(Phase.SHAPE_QA)
        assert len(qa_steps) == 2
        assert all(s.phase == Phase.SHAPE_QA for s in qa_steps)

    def test_get_artifact(self) -> None:
        """Get artifact by type."""
        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="test",
            created_at=datetime.now(),
        )
        spec.artifacts = [
            Artifact(artifact_type=ArtifactType.IDEA_BRIEF, content="Brief"),
            Artifact(artifact_type=ArtifactType.PRODUCT_SPEC, content="Spec"),
        ]
        brief = spec.get_artifact(ArtifactType.IDEA_BRIEF)
        assert brief is not None
        assert brief.content == "Brief"

        # Non-existent artifact returns None
        flight_plan = spec.get_artifact(ArtifactType.FLIGHT_PLAN)
        assert flight_plan is None

    def test_summary(self) -> None:
        """Summary includes statistics."""
        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="test-summary",
            created_at=datetime(2026, 1, 14, 10, 0, 0),
            model="claude-sonnet-4",
        )
        spec.steps = [
            GenerativeStep(
                step_id="1",
                phase=Phase.SHAPE_QA,
                timestamp=datetime.now(),
                input=StepInput(),
                output=StepOutput(content=""),
                metadata=StepMetadata(cost_usd=0.01),
            ),
            GenerativeStep(
                step_id="2",
                phase=Phase.SHAPE_QA,
                timestamp=datetime.now(),
                input=StepInput(),
                output=StepOutput(content=""),
                metadata=StepMetadata(cost_usd=0.02),
            ),
            GenerativeStep(
                step_id="3",
                phase=Phase.CHART,
                timestamp=datetime.now(),
                input=StepInput(),
                output=StepOutput(content=""),
            ),
        ]
        spec.artifacts = [
            Artifact(artifact_type=ArtifactType.IDEA_BRIEF, content=""),
        ]
        spec.decisions = [
            UserDecision(step_id="1", phase=Phase.CHART, decision=DecisionType.ACCEPT),
        ]

        summary = spec.summary()
        assert summary["source_project"] == "test-summary"
        assert summary["total_steps"] == 3
        assert summary["total_decisions"] == 1
        assert summary["total_artifacts"] == 1
        assert summary["phases"]["shape_qa"] == 2
        assert summary["phases"]["chart"] == 1
        assert summary["total_cost_usd"] == 0.03
        assert summary["model"] == "claude-sonnet-4"


class TestGenSpecSerialization:
    """Integration tests for full genspec serialization."""

    def test_roundtrip_to_jsonl(self, tmp_path: Path) -> None:
        """Serialize and deserialize a complete spec."""
        from waypoints.genspec.exporter import export_to_file

        # Create a spec with all components
        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="roundtrip-test",
            created_at=datetime(2026, 1, 14, 10, 0, 0),
            model="claude-sonnet-4",
            initial_idea="Test idea",
        )
        spec.steps = [
            GenerativeStep(
                step_id="step-001",
                phase=Phase.SHAPE_QA,
                timestamp=datetime(2026, 1, 14, 10, 30, 0),
                input=StepInput(
                    system_prompt="You are helpful",
                    user_prompt="Tell me about it",
                ),
                output=StepOutput(content="Here is info", output_type=OutputType.TEXT),
            ),
        ]
        spec.artifacts = [
            Artifact(
                artifact_type=ArtifactType.IDEA_BRIEF,
                content="# Brief\n\nContent",
            ),
        ]

        # Write to file
        output_path = tmp_path / "test.genspec.jsonl"
        export_to_file(spec, output_path)

        # Verify file content
        with open(output_path) as f:
            lines = f.readlines()

        assert len(lines) == 3  # header + 1 step + 1 artifact

        header = json.loads(lines[0])
        assert header["_schema"] == "genspec"
        assert header["source_project"] == "roundtrip-test"

        step = json.loads(lines[1])
        assert step["type"] == "step"
        assert step["step_id"] == "step-001"

        artifact = json.loads(lines[2])
        assert artifact["type"] == "artifact"
        assert artifact["artifact_type"] == "idea_brief"

    def test_import_from_file(self, tmp_path: Path) -> None:
        """Import a genspec file."""
        from waypoints.genspec.exporter import export_to_file
        from waypoints.genspec.importer import import_from_file

        # Create a spec
        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="import-test",
            created_at=datetime(2026, 1, 14, 10, 0, 0),
            initial_idea="Test idea for import",
        )
        spec.steps = [
            GenerativeStep(
                step_id="step-001",
                phase=Phase.SHAPE_BRIEF,
                timestamp=datetime(2026, 1, 14, 10, 30, 0),
                input=StepInput(user_prompt="Generate brief"),
                output=StepOutput(content="# Brief", output_type=OutputType.MARKDOWN),
            ),
        ]
        spec.artifacts = [
            Artifact(
                artifact_type=ArtifactType.FLIGHT_PLAN,
                content='[{"id": "WP-1", "title": "Test", "objective": "Test obj"}]',
            ),
        ]

        # Export to file
        output_path = tmp_path / "import-test.genspec.jsonl"
        export_to_file(spec, output_path)

        # Import from file
        imported_spec = import_from_file(output_path)

        # Verify
        assert imported_spec.source_project == "import-test"
        assert imported_spec.initial_idea == "Test idea for import"
        assert len(imported_spec.steps) == 1
        assert imported_spec.steps[0].step_id == "step-001"
        assert len(imported_spec.artifacts) == 1
        assert imported_spec.artifacts[0].artifact_type == ArtifactType.FLIGHT_PLAN

    def test_validate_spec_valid(self) -> None:
        """Valid spec passes validation."""
        from waypoints.genspec.importer import validate_spec

        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="valid-test",
            created_at=datetime.now(),
            initial_idea="An idea",
        )
        spec.artifacts = [
            Artifact(artifact_type=ArtifactType.IDEA_BRIEF, content="Brief"),
            Artifact(artifact_type=ArtifactType.PRODUCT_SPEC, content="Spec"),
            Artifact(
                artifact_type=ArtifactType.FLIGHT_PLAN,
                content='[{"id": "WP-1", "title": "Test", "objective": "Obj"}]',
            ),
        ]
        spec.steps = [
            GenerativeStep(
                step_id="1",
                phase=Phase.SHAPE_QA,
                timestamp=datetime.now(),
                input=StepInput(),
                output=StepOutput(content=""),
            ),
        ]

        result = validate_spec(spec)
        assert result.valid
        assert not result.has_errors
        assert not result.has_warnings

    def test_validate_spec_missing_flight_plan(self) -> None:
        """Missing flight plan fails validation."""
        from waypoints.genspec.importer import validate_spec

        spec = GenerativeSpec(
            version="1.0",
            waypoints_version="0.1.0",
            source_project="invalid-test",
            created_at=datetime.now(),
            initial_idea="An idea",
        )
        spec.artifacts = [
            Artifact(artifact_type=ArtifactType.IDEA_BRIEF, content="Brief"),
        ]

        result = validate_spec(spec)
        assert not result.valid
        assert result.has_errors
        assert any("flight_plan" in e.lower() for e in result.errors)


def test_create_project_respects_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Projects are created under the configured project_directory."""
    from waypoints import config
    from waypoints.config.paths import reset_paths
    from waypoints.genspec.importer import create_project_from_spec

    reset_paths()  # Ensure get_paths() doesn't carry a previous workspace
    custom_root = tmp_path / "custom-projects"
    monkeypatch.setattr(
        config.settings, "_data", {"project_directory": str(custom_root)}
    )

    spec = GenerativeSpec(
        version="1.0",
        waypoints_version="0.1.0",
        source_project="source",
        created_at=datetime.now(),
        initial_idea="idea",
        artifacts=[
            Artifact(
                artifact_type=ArtifactType.FLIGHT_PLAN,
                content=json.dumps(
                    [
                        {
                            "id": "WP-1",
                            "title": "Test",
                            "objective": "Obj",
                            "acceptance_criteria": [],
                        }
                    ]
                ),
            )
        ],
    )

    project = create_project_from_spec(spec, name="Imported Project", replay_mode=True)

    assert project.get_path().is_relative_to(custom_root.resolve())
    assert project.slug.startswith("imported-project")


def test_import_from_bundle(tmp_path: Path) -> None:
    """Importing from a bundle zip reads the embedded genspec."""
    from waypoints.genspec.exporter import export_bundle
    from waypoints.genspec.importer import import_from_file

    spec = GenerativeSpec(
        version="1.0",
        waypoints_version="0.1.0",
        source_project="bundle-import",
        created_at=datetime.now(),
        initial_idea="Idea",
    )
    spec.steps = [
        GenerativeStep(
            step_id="step-001",
            phase=Phase.SHAPE_QA,
            timestamp=datetime.now(),
            input=StepInput(user_prompt="Q"),
            output=StepOutput(content="A", output_type=OutputType.TEXT),
        )
    ]
    spec.artifacts = [Artifact(artifact_type=ArtifactType.IDEA_BRIEF, content="Brief")]

    bundle_path = tmp_path / "bundle.genspec.zip"
    export_bundle(spec, bundle_path)

    imported = import_from_file(bundle_path)

    assert imported.source_project == "bundle-import"
    assert len(imported.steps) == 1


def test_import_from_bundle_checksum_mismatch(tmp_path: Path) -> None:
    """Importing a tampered bundle fails checksum verification."""
    import zipfile

    from waypoints.genspec.exporter import export_bundle
    from waypoints.genspec.importer import import_from_file

    spec = GenerativeSpec(
        version="1.0",
        waypoints_version="0.1.0",
        source_project="bundle-import",
        created_at=datetime.now(),
        initial_idea="Idea",
    )
    spec.steps = [
        GenerativeStep(
            step_id="step-001",
            phase=Phase.SHAPE_QA,
            timestamp=datetime.now(),
            input=StepInput(user_prompt="Q"),
            output=StepOutput(content="A", output_type=OutputType.TEXT),
        )
    ]
    spec.artifacts = [Artifact(artifact_type=ArtifactType.IDEA_BRIEF, content="Brief")]

    original_path = tmp_path / "bundle.genspec.zip"
    export_bundle(spec, original_path)

    tampered_path = tmp_path / "bundle-tampered.genspec.zip"
    with zipfile.ZipFile(original_path) as original:
        entries = {name: original.read(name) for name in original.namelist()}

    checksums = json.loads(entries["checksums.json"].decode("utf-8"))
    checksums["files"]["genspec.jsonl"] = "0" * 64
    entries["checksums.json"] = (
        json.dumps(checksums, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )

    with zipfile.ZipFile(tampered_path, mode="w") as tampered:
        for name, content in entries.items():
            tampered.writestr(name, content)

    with pytest.raises(ValueError, match="Checksum mismatch"):
        import_from_file(tampered_path)
