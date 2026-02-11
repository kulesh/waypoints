"""Tests for development covenant loading and guidance projection."""

from pathlib import Path

from waypoints.fly.covenant import (
    CovenantGuidanceInput,
    build_guidance_packet,
    load_development_covenant,
)
from waypoints.fly.protocol import FlyRole


def test_load_development_covenant_prefers_agents_file(tmp_path: Path) -> None:
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("# Team policy\nAlways test first.\n", encoding="utf-8")

    covenant = load_development_covenant(tmp_path)

    assert covenant.source_path is not None
    assert covenant.source_path.endswith("AGENTS.md")
    assert "Always test first" in covenant.content
    assert covenant.policy_hash
    assert covenant.covenant_version.startswith("sha256:")


def test_load_development_covenant_falls_back_when_missing(tmp_path: Path) -> None:
    covenant = load_development_covenant(tmp_path)

    assert covenant.source_path is None
    assert "Development Covenant" in covenant.content
    assert covenant.policy_hash


def test_build_guidance_packet_projects_covenant_metadata(tmp_path: Path) -> None:
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("# Team policy\nPrefer simple designs.\n", encoding="utf-8")
    covenant = load_development_covenant(tmp_path)

    packet = build_guidance_packet(
        covenant,
        CovenantGuidanceInput(
            waypoint_id="WP-777",
            role=FlyRole.BUILDER,
            role_constraints=("Ship with tests",),
            stop_conditions=("Ask clarification on doubt",),
        ),
    )

    assert packet.waypoint_id == "WP-777"
    assert packet.produced_by_role == FlyRole.ORCHESTRATOR
    assert packet.covenant_version == covenant.covenant_version
    assert packet.policy_hash == covenant.policy_hash
    assert packet.role_constraints == ("Ship with tests",)
    assert packet.stop_conditions == ("Ask clarification on doubt",)
