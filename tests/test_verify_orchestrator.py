"""Deterministic tests for verify orchestrator path handling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from waypoints.verify import orchestrator


def test_find_idea_brief_prefers_docs_newest(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    first = docs / "idea-brief-2026-01-01.md"
    second = docs / "idea-brief-2026-01-02.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    found = orchestrator._find_idea_brief(tmp_path)

    assert found == second


def test_find_product_spec_falls_back_to_root(tmp_path: Path) -> None:
    spec = tmp_path / "product-spec-latest.md"
    spec.write_text("spec", encoding="utf-8")

    found = orchestrator._find_product_spec(tmp_path)

    assert found == spec


def test_find_flight_plan_returns_none_when_missing(tmp_path: Path) -> None:
    assert orchestrator._find_flight_plan(tmp_path) is None


def test_run_verification_returns_error_for_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    code = orchestrator.run_verification(missing)
    assert code == 2


def test_run_verification_returns_error_when_brief_missing(tmp_path: Path) -> None:
    code = orchestrator.run_verification(tmp_path)
    assert code == 2


def test_run_verification_dispatches_to_verify(
    monkeypatch: Any, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    brief = docs / "idea-brief-2026-02-07.md"
    brief.write_text("brief", encoding="utf-8")

    called: dict[str, bool] = {"verify": False}

    def fake_run_verify(
        genspec_dir: Path,
        brief_content: str,
        reference_dir: Path,
        output_dir: Path,
        skip_fly: bool,
        verbose: bool,
    ) -> int:
        _ = (reference_dir, output_dir, skip_fly, verbose)
        called["verify"] = True
        assert genspec_dir == tmp_path.resolve()
        assert brief_content == "brief"
        return 0

    monkeypatch.setattr(orchestrator, "_run_verify", fake_run_verify)

    code = orchestrator.run_verification(tmp_path, bootstrap=False, skip_fly=True)

    assert code == 0
    assert called["verify"] is True
