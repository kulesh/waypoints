"""Tests for fly git commit/rollback policy helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from waypoints.git.config import GitConfig
from waypoints.models.waypoint import Waypoint
from waypoints.orchestration.fly_git import commit_waypoint, rollback_to_tag


class _ProjectStub:
    def __init__(self, root: Path, slug: str = "demo") -> None:
        self._root = root
        self.slug = slug

    def get_path(self) -> Path:
        return self._root


class _GitStub:
    def __init__(
        self,
        *,
        is_repo: bool = True,
        commit_success: bool = True,
        commit_message: str = "ok",
        reset_success: bool = True,
    ) -> None:
        self._is_repo = is_repo
        self._commit_success = commit_success
        self._commit_message = commit_message
        self._reset_success = reset_success
        self.init_calls = 0
        self.stage_calls: list[str] = []
        self.commit_calls: list[str] = []
        self.tag_calls: list[tuple[str, str]] = []
        self.reset_calls: list[str] = []

    def is_git_repo(self) -> bool:
        return self._is_repo

    def init_repo(self) -> SimpleNamespace:
        self.init_calls += 1
        self._is_repo = True
        return SimpleNamespace(success=True, message="init ok")

    def stage_project_files(self, slug: str) -> None:
        self.stage_calls.append(slug)

    def commit(self, message: str) -> SimpleNamespace:
        self.commit_calls.append(message)
        return SimpleNamespace(
            success=self._commit_success,
            message=self._commit_message,
        )

    def get_head_commit(self) -> str:
        return "deadbeef"

    def tag(self, name: str, message: str) -> None:
        self.tag_calls.append((name, message))

    def reset_hard(self, tag: str) -> SimpleNamespace:
        self.reset_calls.append(tag)
        return SimpleNamespace(success=self._reset_success, message="reset")


def _waypoint() -> Waypoint:
    return Waypoint(id="WP-101", title="Ship it", objective="Ship it")


def test_commit_waypoint_respects_auto_commit_disabled(tmp_path: Path) -> None:
    project = _ProjectStub(tmp_path)
    git = _GitStub()
    config = GitConfig(auto_commit=False)

    result = commit_waypoint(project, _waypoint(), git_config=config, git_service=git)

    assert result.committed is False
    assert result.message == "Auto-commit disabled"
    assert git.stage_calls == []
    assert git.commit_calls == []


def test_commit_waypoint_requires_repo_without_auto_init(tmp_path: Path) -> None:
    project = _ProjectStub(tmp_path)
    git = _GitStub(is_repo=False)
    config = GitConfig(auto_commit=True, auto_init=False, run_checklist=False)

    result = commit_waypoint(project, _waypoint(), git_config=config, git_service=git)

    assert result.committed is False
    assert "Not a git repo" in result.message


def test_commit_waypoint_creates_tag_when_enabled(tmp_path: Path) -> None:
    project = _ProjectStub(tmp_path, slug="demo")
    git = _GitStub(is_repo=True)
    config = GitConfig(
        auto_commit=True,
        auto_init=True,
        run_checklist=False,
        create_waypoint_tags=True,
    )

    result = commit_waypoint(project, _waypoint(), git_config=config, git_service=git)

    assert result.committed is True
    assert result.commit_hash == "deadbeef"
    assert result.tag_name == "demo/WP-101"
    assert git.tag_calls == [("demo/WP-101", "Completed waypoint: Ship it")]


def test_commit_waypoint_fails_when_receipt_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _ProjectStub(tmp_path)
    git = _GitStub(is_repo=True)
    config = GitConfig(auto_commit=True, auto_init=True, run_checklist=True)

    class _Validator:
        def find_latest_receipt(self, project_obj: object, waypoint_id: str) -> None:
            _ = (project_obj, waypoint_id)
            return None

    monkeypatch.setattr("waypoints.orchestration.fly_git.ReceiptValidator", _Validator)

    result = commit_waypoint(project, _waypoint(), git_config=config, git_service=git)

    assert result.committed is False
    assert "No receipt found" in result.message


def test_rollback_to_tag_loads_flight_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _ProjectStub(tmp_path)
    git = _GitStub(is_repo=True, reset_success=True)
    loaded_plan = object()

    class _Reader:
        @staticmethod
        def load(project_obj: object) -> object:
            _ = project_obj
            return loaded_plan

    monkeypatch.setattr("waypoints.orchestration.fly_git.FlightPlanReader", _Reader)

    result = rollback_to_tag(project, "demo/WP-101", git_service=git)

    assert result.success is True
    assert result.flight_plan is loaded_plan
    assert git.reset_calls == ["demo/WP-101"]
