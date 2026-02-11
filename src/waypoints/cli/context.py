"""Shared context helpers for CLI command modules."""

from __future__ import annotations

import sys
from pathlib import Path

from waypoints.config.project_root import get_projects_root
from waypoints.models.project import Project


def project_path_for_slug(slug: str) -> Path:
    """Resolve project path for a slug under configured projects root."""
    return get_projects_root() / slug


def load_project_or_error(slug: str) -> Project | None:
    """Load project by slug or print a user-facing error and return None."""
    projects_root = get_projects_root()
    project_path = projects_root / slug

    if not project_path.exists():
        print(f"Error: Project '{slug}' not found", file=sys.stderr)
        print(f"  Looked in: {projects_root}", file=sys.stderr)
        return None

    try:
        return Project.load(slug)
    except FileNotFoundError:
        print(f"Error: Could not load project '{slug}'", file=sys.stderr)
        return None
