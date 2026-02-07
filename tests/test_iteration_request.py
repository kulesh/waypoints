"""Tests for iteration request persistence and lifecycle tracking."""

from datetime import UTC, datetime
from pathlib import Path

from waypoints.models.iteration_request import (
    IterationAttachmentRecord,
    IterationIntent,
    IterationRequestReader,
    IterationRequestStatus,
    IterationRequestWriter,
    IterationTriage,
)


class MockProject:
    """Minimal project surface needed by iteration request persistence."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.slug = "test-project"

    def get_sessions_path(self) -> Path:
        sessions = self._root / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        return sessions


def _triage(intent: IterationIntent = IterationIntent.BUG_FIX) -> IterationTriage:
    return IterationTriage(
        intent=intent,
        confidence=0.8,
        summary="Header alignment bug",
        rationale="Contains explicit bug terms",
        source="heuristic",
    )


def test_iteration_request_writer_records_submission(tmp_path: Path) -> None:
    project = MockProject(tmp_path)
    writer = IterationRequestWriter(project)

    record = writer.log_submitted(
        prompt="Fix the header spacing regression",
        triage=_triage(),
        attachments=(
            IterationAttachmentRecord(
                relative_path="evidence/iterations/screen.png",
                sha256="a" * 64,
                size_bytes=1234,
                source_path="/tmp/screen.png",
            ),
        ),
    )

    loaded = IterationRequestReader.load(project)
    assert len(loaded) == 1
    assert loaded[0].request_id == record.request_id
    assert loaded[0].status == IterationRequestStatus.SUBMITTED
    assert loaded[0].triage.intent == IterationIntent.BUG_FIX
    assert loaded[0].attachments[0].relative_path == "evidence/iterations/screen.png"


def test_iteration_request_writer_tracks_lifecycle(tmp_path: Path) -> None:
    project = MockProject(tmp_path)
    writer = IterationRequestWriter(project)

    record = writer.log_submitted(
        prompt="Fix the save button crash",
        triage=_triage(),
    )
    writer.log_waypoint_drafted(
        record.request_id, draft_waypoint_id="WP-016", insert_after="WP-015"
    )
    writer.log_waypoint_added(
        record.request_id, waypoint_id="WP-016", insert_after="WP-015"
    )

    history = IterationRequestReader.load_history(project)
    latest = IterationRequestReader.load(project)[0]

    assert len(history) == 3
    assert latest.status == IterationRequestStatus.WAYPOINT_ADDED
    assert latest.draft_waypoint_id == "WP-016"
    assert latest.linked_waypoint_id == "WP-016"
    assert latest.insert_after == "WP-015"


def test_iteration_request_writer_tracks_cancel_and_failure(tmp_path: Path) -> None:
    project = MockProject(tmp_path)
    writer = IterationRequestWriter(project)

    cancelled = writer.log_submitted(
        prompt="Improve onboarding copy",
        triage=_triage(IterationIntent.IMPROVEMENT),
    )
    failed = writer.log_submitted(
        prompt="Fix null pointer in checkout flow",
        triage=_triage(),
    )

    writer.log_cancelled(cancelled.request_id)
    writer.log_generation_failed(failed.request_id, "provider timeout")

    latest = IterationRequestReader.load_map(project)
    assert latest[cancelled.request_id].status == IterationRequestStatus.CANCELLED
    assert latest[failed.request_id].status == IterationRequestStatus.GENERATION_FAILED
    assert latest[failed.request_id].error_message == "provider timeout"


def test_iteration_request_roundtrip_preserves_timestamps() -> None:
    created = datetime(2026, 2, 7, 16, 0, 0, tzinfo=UTC)
    updated = datetime(2026, 2, 7, 16, 5, 0, tzinfo=UTC)
    triage = IterationTriage(
        intent=IterationIntent.BUG_FIX_AND_IMPROVEMENT,
        confidence=0.91,
        summary="Fix flaky save flow and improve error text",
        rationale="Contains both bug and enhancement cues",
        source="llm",
    )

    from waypoints.models.iteration_request import IterationRequestRecord

    record = IterationRequestRecord(
        request_id="ir-123",
        prompt="Fix save flow and improve message clarity",
        triage=triage,
        created_at=created,
        updated_at=updated,
        status=IterationRequestStatus.WAYPOINT_DRAFTED,
        draft_waypoint_id="WP-120",
    )

    restored = IterationRequestRecord.from_dict(record.to_dict())
    assert restored.created_at == created
    assert restored.updated_at == updated
    assert restored.triage.intent == IterationIntent.BUG_FIX_AND_IMPROVEMENT
