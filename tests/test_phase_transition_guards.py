"""Regression tests for screen-level transition guards."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from waypoints.models import Journey, JourneyState
from waypoints.models.dialogue import DialogueHistory
from waypoints.tui.screens.idea_brief import IdeaBriefScreen
from waypoints.tui.screens.product_spec import ProductSpecScreen
from waypoints.tui.screens.transition_guard import can_enter_state


def _journey_at(state: JourneyState) -> Journey:
    """Create a journey positioned at the requested state."""
    path = [
        JourneyState.SPARK_IDLE,
        JourneyState.SPARK_ENTERING,
        JourneyState.SHAPE_QA,
        JourneyState.SHAPE_BRIEF_GENERATING,
        JourneyState.SHAPE_BRIEF_REVIEW,
        JourneyState.SHAPE_SPEC_GENERATING,
        JourneyState.SHAPE_SPEC_REVIEW,
        JourneyState.CHART_GENERATING,
        JourneyState.CHART_REVIEW,
        JourneyState.FLY_READY,
        JourneyState.FLY_EXECUTING,
        JourneyState.LAND_REVIEW,
    ]
    journey = Journey.new("demo")
    for next_state in path[1 : path.index(state) + 1]:
        journey = journey.transition(next_state)
    return journey


class _FakeProject:
    def __init__(self, docs_path: Path, journey: Journey | None) -> None:
        self.name = "Demo"
        self.slug = "demo"
        self.initial_idea = "idea"
        self.journey = journey
        self._docs_path = docs_path

    def get_docs_path(self) -> Path:
        self._docs_path.mkdir(parents=True, exist_ok=True)
        return self._docs_path


def test_can_enter_state_allows_same_state() -> None:
    journey = _journey_at(JourneyState.SHAPE_SPEC_GENERATING)
    assert can_enter_state(journey, JourneyState.SHAPE_SPEC_GENERATING)


def test_idea_brief_continue_blocked_while_generating(tmp_path: Path) -> None:
    project = _FakeProject(
        docs_path=tmp_path / "docs",
        journey=_journey_at(JourneyState.SHAPE_BRIEF_GENERATING),
    )
    screen = IdeaBriefScreen(
        project=project,
        idea="idea",
        history=DialogueHistory(),
    )

    notifications: list[str] = []
    switch_calls: list[tuple[str, dict[str, object]]] = []
    screen._app = SimpleNamespace(
        switch_phase=lambda phase, data: switch_calls.append((phase, data)),
        call_later=lambda _cb: None,
    )
    screen.notify = lambda message, **_kwargs: notifications.append(str(message))  # type: ignore[method-assign]
    screen._save_to_disk = lambda: (_ for _ in ()).throw(
        AssertionError("unexpected save")
    )  # type: ignore[method-assign]
    screen._is_generating = True

    screen.action_proceed_to_spec()

    assert switch_calls == []
    assert notifications == ["Idea brief generation is still running. Please wait."]


def test_idea_brief_continue_redirects_when_state_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _FakeProject(
        docs_path=tmp_path / "docs",
        journey=_journey_at(JourneyState.SHAPE_QA),
    )
    screen = IdeaBriefScreen(
        project=project,
        idea="idea",
        history=DialogueHistory(),
    )
    screen.brief_content = "brief"

    notifications: list[str] = []
    switch_calls: list[tuple[str, dict[str, object]]] = []
    redirect_calls: list[object] = []
    test_app = SimpleNamespace(
        switch_phase=lambda phase, data: switch_calls.append((phase, data)),
        call_later=lambda cb: redirect_calls.append(cb),
    )
    monkeypatch.setattr(IdeaBriefScreen, "app", property(lambda _self: test_app))
    screen.notify = lambda message, **_kwargs: notifications.append(str(message))  # type: ignore[method-assign]
    screen._save_to_disk = lambda: (_ for _ in ()).throw(
        AssertionError("unexpected save")
    )  # type: ignore[method-assign]

    screen.action_proceed_to_spec()

    assert switch_calls == []
    assert len(redirect_calls) == 1
    assert notifications == [
        "Current state changed. Redirecting to the correct screen."
    ]


def test_product_spec_continue_blocked_while_generating(tmp_path: Path) -> None:
    project = _FakeProject(
        docs_path=tmp_path / "docs",
        journey=_journey_at(JourneyState.SHAPE_SPEC_GENERATING),
    )
    screen = ProductSpecScreen(project=project, brief="brief")
    screen.spec_content = "spec"

    notifications: list[str] = []
    switch_calls: list[tuple[str, dict[str, object]]] = []
    screen._app = SimpleNamespace(
        switch_phase=lambda phase, data: switch_calls.append((phase, data)),
        call_later=lambda _cb: None,
    )
    screen.notify = lambda message, **_kwargs: notifications.append(str(message))  # type: ignore[method-assign]
    screen._save_to_disk = lambda: (_ for _ in ()).throw(
        AssertionError("unexpected save")
    )  # type: ignore[method-assign]
    screen._is_generating = True

    screen.action_proceed_to_waypoints()

    assert switch_calls == []
    assert notifications == ["Product spec generation is still running. Please wait."]
