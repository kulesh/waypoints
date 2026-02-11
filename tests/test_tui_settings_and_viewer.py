from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from waypoints.config.settings import settings
from waypoints.tui.screens.genspec_viewer import GenSpecViewerScreen
from waypoints.tui.screens.settings import SettingsModal


def _component_lookup(
    components: dict[str, object],
) -> Any:
    def _query_one(selector: str, _type: object | None = None) -> Any:
        return components[selector]

    return _query_one


@dataclass
class _FakeInput:
    value: str


@dataclass
class _FakeSelect:
    value: str


@dataclass
class _FakeSwitch:
    value: bool


@dataclass
class _FakeRow:
    display: bool = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.set_spec_calls: list[dict[str, Any]] = []
        self.focused = False

    def set_spec(
        self,
        spec: Any,
        *,
        source_label: str,
        metadata: dict[str, Any] | None,
        checksums: dict[str, str],
        select_first: bool,
    ) -> None:
        self.set_spec_calls.append(
            {
                "spec": spec,
                "source_label": source_label,
                "metadata": metadata,
                "checksums": checksums,
                "select_first": select_first,
            }
        )

    def focus_tree(self) -> None:
        self.focused = True


def test_settings_modal_save_settings_rejects_non_numeric_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modal = SettingsModal()
    notifications: list[tuple[str, str]] = []
    components = {
        "#project-dir-input": _FakeInput("/tmp/waypoints"),
        "#provider-select": _FakeSelect("anthropic"),
        "#model-input": _FakeInput("claude-sonnet-4-5-20241022"),
        "#budget-input": _FakeInput("not-a-number"),
        "#web-auth-switch": _FakeSwitch(True),
        "#openai-key-input": _FakeInput(""),
        "#anthropic-key-input": _FakeInput(""),
    }

    monkeypatch.setattr(modal, "query_one", _component_lookup(components))
    monkeypatch.setattr(
        modal,
        "notify",
        lambda message, *, severity="information": notifications.append(
            (str(message), str(severity))
        ),
    )

    result = modal._save_settings()

    assert result is False
    assert notifications == [("Budget must be a number", "error")]


def test_settings_modal_update_provider_ui_toggles_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modal = SettingsModal()
    provider = _FakeSelect("openai")
    web_auth_row = _FakeRow()
    anthropic_key_row = _FakeRow()
    openai_key_row = _FakeRow()
    components = {
        "#provider-select": provider,
        "#web-auth-row": web_auth_row,
        "#anthropic-key-row": anthropic_key_row,
        "#openai-key-row": openai_key_row,
    }

    monkeypatch.setattr(modal, "query_one", _component_lookup(components))

    modal._update_provider_ui()
    assert web_auth_row.display is False
    assert anthropic_key_row.display is False
    assert openai_key_row.display is True

    provider.value = "anthropic"
    modal._update_provider_ui()
    assert web_auth_row.display is True
    assert anthropic_key_row.display is True
    assert openai_key_row.display is False


def test_settings_modal_save_settings_accepts_valid_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modal = SettingsModal()
    notifications: list[tuple[str, str]] = []
    components = {
        "#project-dir-input": _FakeInput("/tmp/waypoints"),
        "#provider-select": _FakeSelect("openai"),
        "#model-input": _FakeInput("gpt-5.2"),
        "#budget-input": _FakeInput("42.5"),
        "#web-auth-switch": _FakeSwitch(False),
        "#openai-key-input": _FakeInput("sk-test"),
        "#anthropic-key-input": _FakeInput(""),
    }

    monkeypatch.setattr(modal, "query_one", _component_lookup(components))
    monkeypatch.setattr(
        modal,
        "notify",
        lambda message, *, severity="information": notifications.append(
            (str(message), str(severity))
        ),
    )

    result = modal._save_settings()

    assert result is True
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-5.2"
    assert settings.llm_budget_usd == pytest.approx(42.5)
    assert ("Settings saved", "information") in notifications


def test_genspec_viewer_load_spec_populates_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = GenSpecViewerScreen(path=Path("demo.genspec.jsonl"))
    browser = _FakeBrowser()
    notifications: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "waypoints.genspec.viewer.load_genspec",
        lambda _path: ({"version": 1}, None, {"manifest": "abc"}),
    )
    monkeypatch.setattr(screen, "query_one", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr(
        screen,
        "notify",
        lambda message, *, severity="information": notifications.append(
            (str(message), str(severity))
        ),
    )

    screen._load_spec()

    assert notifications == []
    assert len(browser.set_spec_calls) == 1
    assert browser.set_spec_calls[0]["source_label"] == "jsonl"
    assert browser.focused is True


def test_genspec_viewer_load_spec_surfaces_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = GenSpecViewerScreen(path=Path("broken.genspec.jsonl"))
    notifications: list[tuple[str, str]] = []

    def _raise(_path: Path) -> tuple[object, object, object]:
        raise ValueError("broken spec")

    monkeypatch.setattr("waypoints.genspec.viewer.load_genspec", _raise)
    monkeypatch.setattr(
        screen,
        "notify",
        lambda message, *, severity="information": notifications.append(
            (str(message), str(severity))
        ),
    )

    screen._load_spec()

    assert notifications == [("Failed to load: broken spec", "error")]
