"""Tests for LAND iteration intake helpers."""

from pathlib import Path

import pytest

from waypoints.llm import StreamChunk, StreamComplete
from waypoints.models.iteration_request import IterationIntent
from waypoints.orchestration.iteration_service import (
    IterationAttachment,
    IterationRequestService,
)


def test_extract_existing_files_supports_quoted_and_relative_paths(
    tmp_path: Path,
) -> None:
    """Extract file paths from freeform request text."""
    project_path = tmp_path / "project"
    project_path.mkdir()

    image = project_path / "screenshots" / "error shot.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n")

    notes = project_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")

    text = (
        'Layout is broken. See "screenshots/error shot.png" and ./notes.txt. '
        "Ignore https://example.com/image.png."
    )

    service = IterationRequestService(project_path)
    files = service.extract_existing_files(text)

    assert files == [image.resolve(), notes.resolve()]


def test_ingest_attachments_copies_to_evidence_snapshot(tmp_path: Path) -> None:
    """Mentioned files are copied into project-local evidence storage."""
    project_path = tmp_path / "project"
    project_path.mkdir()

    source = project_path / "broken.png"
    source.write_bytes(b"fake image")

    service = IterationRequestService(project_path)
    attachments = service.ingest_attachments("./broken.png")

    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.source_path == source.resolve()
    assert attachment.stored_path.exists()
    assert attachment.stored_path.read_bytes() == source.read_bytes()
    assert attachment.relative_path.startswith("evidence/iterations/")
    assert attachment.size_bytes == len(b"fake image")
    assert len(attachment.sha256) == 64


def test_build_waypoint_description_appends_attachment_manifest() -> None:
    """Prompt assembly includes evidence file references when attachments exist."""
    attachment = IterationAttachment(
        source_path=Path("/tmp/original.png"),
        stored_path=Path("/tmp/project/evidence/iterations/a.png"),
        relative_path="evidence/iterations/a.png",
        sha256="a" * 64,
        size_bytes=321,
    )

    description = IterationRequestService.build_waypoint_description(
        "Fix broken spacing near header",
        [attachment],
    )

    assert "Fix broken spacing near header" in description
    assert "Evidence files (project-relative paths):" in description
    assert "evidence/iterations/a.png" in description
    assert "sha256=aaaaaaaaaaaa" in description
    assert "Read tool" in description


def test_classify_request_uses_llm_json_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM triage response is parsed into structured intent metadata."""

    class FakeChatClient:
        def __init__(self, **_: object) -> None:
            pass

        def stream_message(self, **_: object):
            yield StreamChunk(
                text='{"intent":"bug_fix","confidence":0.93,'
                '"summary":"Fix null pointer crash","rationale":"Crash + fix terms"}'
            )
            yield StreamComplete(full_text="", cost_usd=0.0)

    monkeypatch.setattr(
        "waypoints.orchestration.iteration_service.ChatClient",
        FakeChatClient,
    )

    service = IterationRequestService(tmp_path)
    triage = service.classify_request(
        "Fix checkout crash when cart is empty",
        attachments=[],
    )

    assert triage.source == "llm"
    assert triage.intent == IterationIntent.BUG_FIX
    assert triage.confidence == 0.93
    assert "null pointer" in triage.summary


def test_classify_request_falls_back_to_heuristics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heuristic classifier is used when LLM triage fails."""

    class FailingChatClient:
        def __init__(self, **_: object) -> None:
            pass

        def stream_message(self, **_: object):
            raise RuntimeError("service unavailable")
            yield StreamChunk(text="")

    monkeypatch.setattr(
        "waypoints.orchestration.iteration_service.ChatClient",
        FailingChatClient,
    )

    service = IterationRequestService(tmp_path)
    triage = service.classify_request(
        "Fix the broken mobile layout and add better spacing",
        attachments=[],
    )

    assert triage.source == "heuristic"
    assert triage.intent == IterationIntent.BUG_FIX_AND_IMPROVEMENT
