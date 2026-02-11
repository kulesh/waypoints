"""Skill pack resolution for multi-agent FLY guidance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillPack:
    """Static skill pack descriptor with deterministic applicability checks."""

    skill_id: str
    version: str
    marker_files: tuple[str, ...]

    def applies(self, project_path: Path) -> bool:
        return any((project_path / marker).exists() for marker in self.marker_files)


SKILL_PACKS: tuple[SkillPack, ...] = (
    SkillPack(
        skill_id="python-pytest-ruff",
        version="1",
        marker_files=("pyproject.toml", "requirements.txt", "requirements-dev.txt"),
    ),
    SkillPack(
        skill_id="typescript-node",
        version="1",
        marker_files=("package.json", "tsconfig.json"),
    ),
    SkillPack(
        skill_id="rust-cargo",
        version="1",
        marker_files=("Cargo.toml",),
    ),
)


def resolve_attached_skills(project_path: Path) -> tuple[str, ...]:
    """Resolve applicable skill IDs for the given project root."""
    attached = [
        f"{skill.skill_id}@{skill.version}"
        for skill in SKILL_PACKS
        if skill.applies(project_path)
    ]
    return tuple(attached)
