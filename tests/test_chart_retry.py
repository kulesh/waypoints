"""Tests for chart generation retries."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoints.config.paths import reset_paths
from waypoints.config.settings import settings
from waypoints.llm.client import StreamChunk
from waypoints.models.project import Project
from waypoints.orchestration.coordinator import JourneyCoordinator


class StubLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls = 0

    def stream_message(
        self, messages: list[dict[str, str]], system: str
    ) -> list[StreamChunk]:
        response = self._responses[self.calls]
        self.calls += 1
        return [StreamChunk(text=response)]


@pytest.fixture(autouse=True)
def reset_paths_singleton() -> None:
    """Reset the paths singleton before each test."""
    reset_paths()


def test_generate_flight_plan_retries_on_validation_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retry once when validation fails due to empty acceptance criteria."""
    monkeypatch.setattr(settings, "_save", lambda: None)
    settings.project_directory = tmp_path
    project = Project.create("Retry Project", idea="Test")
    project.journey.updated_at = datetime.now(UTC)

    invalid = (
        '[{"id":"WP-001","title":"Bad","objective":"Implement the core flow",'
        '"acceptance_criteria":[],'
        '"spec_context_summary":"This waypoint implements core flow behaviors from the'
        " product specification and should be covered by end-to-end checks before"
        ' moving to integration work.",'
        '"spec_section_refs":["Product Specification"]}]'
    )
    valid = (
        '[{"id":"WP-001","title":"Good","objective":"Implement the core flow",'
        '"acceptance_criteria":["Meets requirements"],'
        '"spec_context_summary":"This waypoint implements core flow behaviors from the'
        " product specification and should be covered by end-to-end checks before"
        ' moving to integration work.",'
        '"spec_section_refs":["Product Specification"]}]'
    )
    coordinator = JourneyCoordinator(project=project, llm=StubLLM([invalid, valid]))

    plan = coordinator.generate_flight_plan("spec")

    assert len(plan.waypoints) == 1
    assert plan.waypoints[0].acceptance_criteria == ["Meets requirements"]
    assert coordinator.llm.calls == 2
