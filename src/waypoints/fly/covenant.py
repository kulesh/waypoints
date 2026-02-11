"""Development covenant loading and guidance packet projection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from waypoints.fly.protocol import FlyRole, GuidancePacket

_DEFAULT_COVENANT = """# Development Covenant

Follow repository philosophy and engineering guidelines.

When uncertain:
1. Ask for clarification before risky changes.
2. Prefer canonical product spec over stale summaries.
3. Keep changes minimal, testable, and reversible.
"""


@dataclass(frozen=True, slots=True)
class DevelopmentCovenant:
    """Canonical policy snapshot used to guide role turns."""

    source_path: str | None
    content: str
    covenant_version: str
    policy_hash: str
    loaded_at: datetime


@dataclass(frozen=True, slots=True)
class CovenantGuidanceInput:
    """Inputs needed to project a role-scoped guidance packet."""

    waypoint_id: str
    role: FlyRole
    role_constraints: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    attached_skills: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()


def load_development_covenant(
    project_root: Path,
    *,
    preferred_source: Path | None = None,
) -> DevelopmentCovenant:
    """Load development covenant from repository guidance documents."""
    source_path = _resolve_covenant_source(project_root, preferred_source)
    if source_path is None:
        content = _DEFAULT_COVENANT
        source_text: str | None = None
    else:
        content = source_path.read_text(encoding="utf-8")
        source_text = str(source_path)

    policy_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    covenant_version = f"sha256:{policy_hash[:12]}"

    return DevelopmentCovenant(
        source_path=source_text,
        content=content,
        covenant_version=covenant_version,
        policy_hash=policy_hash,
        loaded_at=datetime.now(UTC),
    )


def build_guidance_packet(
    covenant: DevelopmentCovenant,
    guidance_input: CovenantGuidanceInput,
) -> GuidancePacket:
    """Project covenant snapshot into a GuidancePacket artifact."""
    source_refs = guidance_input.source_refs
    if not source_refs and covenant.source_path:
        source_refs = (covenant.source_path,)

    return GuidancePacket(
        waypoint_id=guidance_input.waypoint_id,
        produced_by_role=FlyRole.ORCHESTRATOR,
        source_refs=source_refs,
        covenant_version=covenant.covenant_version,
        policy_hash=covenant.policy_hash,
        role_constraints=guidance_input.role_constraints,
        stop_conditions=guidance_input.stop_conditions,
        attached_skills=guidance_input.attached_skills,
    )


def _resolve_covenant_source(
    project_root: Path,
    preferred_source: Path | None,
) -> Path | None:
    if preferred_source is not None:
        candidate = preferred_source.resolve()
        if candidate.exists() and candidate.is_file():
            return candidate

    candidates = (
        project_root / "AGENTS.md",
        project_root / ".github" / "copilot-instructions.md",
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    return None
