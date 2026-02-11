"""Execution-time guidance helpers for builder/verifier roles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from waypoints.fly.covenant import (
    CovenantGuidanceInput,
    DevelopmentCovenant,
    build_guidance_packet,
    load_development_covenant,
)
from waypoints.fly.protocol import FlyRole, GuidancePacket
from waypoints.fly.skills import resolve_attached_skills

_BUILDER_ROLE_CONSTRAINTS = (
    "Implement only within waypoint scope.",
    "Run required validations before completion claim.",
    "Do not modify files outside project root.",
)
_BUILDER_STOP_CONDITIONS = (
    "Emit clarification request when policy/intent is ambiguous.",
    "Do not emit completion marker while clarification is unresolved.",
)


@dataclass(frozen=True, slots=True)
class BuilderGuidanceBundle:
    """Snapshot of covenant data required for builder turn guidance."""

    covenant: DevelopmentCovenant
    guidance_packet: GuidancePacket


def load_builder_guidance_bundle(
    project_path: Path,
    *,
    waypoint_id: str,
) -> BuilderGuidanceBundle:
    """Load covenant snapshot and derive baseline builder guidance packet."""
    covenant = load_development_covenant(project_path)
    attached_skills = resolve_attached_skills(project_path)
    guidance_packet = build_guidance_packet(
        covenant,
        CovenantGuidanceInput(
            waypoint_id=waypoint_id,
            role=FlyRole.BUILDER,
            role_constraints=_BUILDER_ROLE_CONSTRAINTS,
            stop_conditions=_BUILDER_STOP_CONDITIONS,
            attached_skills=attached_skills,
        ),
    )
    return BuilderGuidanceBundle(covenant=covenant, guidance_packet=guidance_packet)


def build_builder_turn_guidance(
    *,
    waypoint_id: str,
    covenant: DevelopmentCovenant | None,
    baseline_packet: GuidancePacket | None,
) -> GuidancePacket | None:
    """Build a fresh guidance packet artifact for the current builder turn."""
    if covenant is None or baseline_packet is None:
        return None
    return build_guidance_packet(
        covenant,
        CovenantGuidanceInput(
            waypoint_id=waypoint_id,
            role=FlyRole.BUILDER,
            role_constraints=baseline_packet.role_constraints,
            stop_conditions=baseline_packet.stop_conditions,
            attached_skills=baseline_packet.attached_skills,
            source_refs=baseline_packet.source_refs,
        ),
    )


def build_executor_system_prompt(
    *,
    project_path: Path,
    directory_policy_context: str | None,
    guidance_packet: GuidancePacket | None,
) -> str:
    """Render executor system prompt with optional policy and guidance context."""
    policy_context = ""
    if directory_policy_context:
        policy_context = (
            "\nProject memory policy (generated from repository scan):\n"
            f"{directory_policy_context}\n"
        )

    guidance_context = ""
    if guidance_packet is not None:
        constraints = (
            "\n".join(f"- {item}" for item in guidance_packet.role_constraints)
            if guidance_packet.role_constraints
            else "- No additional constraints."
        )
        guidance_context = (
            "\nDevelopment covenant guidance (mandatory):\n"
            f"- covenant_version: {guidance_packet.covenant_version}\n"
            f"- policy_hash: {guidance_packet.policy_hash}\n"
            f"{constraints}\n"
        )

    return f"""You are implementing a software waypoint as part of a larger project.
You have access to file and bash tools to read, write, and execute code.

**CRITICAL CONSTRAINTS:**
- Your working directory is: {project_path}
- ONLY access files within this directory
- NEVER use absolute paths outside the project
- NEVER use ../ to escape the project directory
{policy_context}
{guidance_context}

Work methodically:
1. First understand the existing codebase
2. Make minimal, focused changes
3. Test after each change
4. Iterate until done

When complete, output the completion marker specified in the instructions."""
