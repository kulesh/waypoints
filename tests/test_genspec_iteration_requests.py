"""Tests for iteration request lineage in genspec export/import."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from waypoints.genspec.exporter import export_project
from waypoints.genspec.importer import create_project_from_spec
from waypoints.genspec.spec import Artifact, ArtifactType, GenerativeSpec
from waypoints.models.iteration_request import (
    IterationIntent,
    IterationRequestStatus,
    IterationRequestWriter,
    IterationTriage,
)
from waypoints.models.project import Project


def _triage() -> IterationTriage:
    return IterationTriage(
        intent=IterationIntent.BUG_FIX,
        confidence=0.86,
        summary="Fix screenshot-confirmed crash",
        rationale="Bug-fix request with concrete failure signal",
        source="heuristic",
    )


def test_export_project_includes_iteration_requests_artifact(tmp_path: Path) -> None:
    from waypoints.config.settings import settings

    settings.project_directory = tmp_path
    project = Project.create("Iteration Export")

    writer = IterationRequestWriter(project)
    record = writer.log_submitted(
        prompt="Fix broken save button using ./evidence/shot.png",
        triage=_triage(),
    )
    writer.log_waypoint_drafted(record.request_id, "WP-101", insert_after="WP-100")
    writer.log_waypoint_added(record.request_id, "WP-101", insert_after="WP-100")

    spec = export_project(project)
    artifact = spec.get_artifact(ArtifactType.ITERATION_REQUESTS)

    assert artifact is not None
    payload = json.loads(artifact.content)
    assert payload[0]["request_id"] == record.request_id
    assert payload[0]["status"] == IterationRequestStatus.WAYPOINT_ADDED.value
    assert artifact.file_path == "sessions/chart/iteration_requests.jsonl"


def test_import_restores_iteration_request_log(tmp_path: Path) -> None:
    from waypoints.config.settings import settings

    settings.project_directory = tmp_path
    spec = GenerativeSpec(
        version="1.0",
        waypoints_version="0.1.0",
        source_project="lineage",
        created_at=datetime.now(),
        initial_idea="lineage idea",
        artifacts=[
            Artifact(
                artifact_type=ArtifactType.FLIGHT_PLAN,
                content=json.dumps(
                    [
                        {
                            "id": "WP-001",
                            "title": "Bootstrap",
                            "objective": "Create baseline setup",
                            "acceptance_criteria": ["Project boots"],
                        }
                    ]
                ),
            ),
            Artifact(
                artifact_type=ArtifactType.ITERATION_REQUESTS,
                content=json.dumps(
                    [
                        {
                            "request_id": "ir-abc",
                            "prompt": "Fix crash from screenshot",
                            "triage": {
                                "intent": "bug_fix",
                                "confidence": 0.9,
                                "summary": "Crash fix",
                                "rationale": "Crash terminology",
                                "source": "llm",
                            },
                            "created_at": "2026-02-07T12:00:00+00:00",
                            "updated_at": "2026-02-07T12:01:00+00:00",
                            "attachments": [],
                            "status": "waypoint_added",
                            "linked_waypoint_id": "WP-010",
                        }
                    ]
                ),
            ),
        ],
    )

    project = create_project_from_spec(
        spec, name="Imported Iteration", replay_mode=True
    )
    log_path = project.get_sessions_path() / "chart" / "iteration_requests.jsonl"

    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["request_id"] == "ir-abc"
    assert entry["status"] == "waypoint_added"
