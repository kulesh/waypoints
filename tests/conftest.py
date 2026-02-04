from __future__ import annotations

import copy
from collections.abc import Iterator

import pytest

from waypoints.config.settings import settings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent tests from persisting settings to disk."""
    original_data = copy.deepcopy(settings._data)

    def _noop_save() -> None:
        return None

    monkeypatch.setattr(settings, "_save", _noop_save)
    try:
        yield
    finally:
        settings._data = original_data
