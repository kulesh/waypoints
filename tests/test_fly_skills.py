from __future__ import annotations

from pathlib import Path

from waypoints.fly.skills import resolve_attached_skills


def test_resolve_attached_skills_empty_when_no_markers(tmp_path: Path) -> None:
    assert resolve_attached_skills(tmp_path) == ()


def test_resolve_attached_skills_detects_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    assert resolve_attached_skills(tmp_path) == ("python-pytest-ruff@1",)


def test_resolve_attached_skills_detects_multiple_stacks_in_fixed_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "Cargo.toml").write_text("[package]\nname='demo'\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

    assert resolve_attached_skills(tmp_path) == (
        "python-pytest-ruff@1",
        "typescript-node@1",
        "rust-cargo@1",
    )
