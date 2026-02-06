from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from waypoints.config.settings import settings


def test_settings_do_not_write_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_module = importlib.import_module("waypoints.config.settings")
    monkeypatch.setattr(settings_module, "get_settings_path", lambda: settings_path)

    settings.project_directory = tmp_path

    assert settings.get("project_directory") == str(tmp_path)
    assert not settings_path.exists()


def test_llm_budget_round_trip() -> None:
    settings.llm_budget_usd = 25.0
    assert settings.llm_budget_usd == pytest.approx(25.0)

    settings.llm_budget_usd = None
    assert settings.llm_budget_usd is None


def test_llm_budget_invalid_data_is_ignored() -> None:
    settings._data["llm"] = {"budget_usd": "not-a-number"}
    assert settings.llm_budget_usd is None
