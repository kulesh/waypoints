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
