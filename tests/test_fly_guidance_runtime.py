from __future__ import annotations

from pathlib import Path

from waypoints.fly.guidance_runtime import load_builder_guidance_bundle


def test_load_builder_guidance_bundle_attaches_skills(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")

    bundle = load_builder_guidance_bundle(tmp_path, waypoint_id="WP-200")

    assert bundle.guidance_packet.waypoint_id == "WP-200"
    assert bundle.guidance_packet.attached_skills == ("python-pytest-ruff@1",)


def test_load_builder_guidance_bundle_has_empty_skills_without_markers(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    bundle = load_builder_guidance_bundle(tmp_path, waypoint_id="WP-201")
    assert bundle.guidance_packet.attached_skills == ()
