"""Utilities for resolving the projects root directory."""
from __future__ import annotations

from pathlib import Path

from waypoints.config.settings import settings


def get_projects_root() -> Path:
    """Return the resolved projects root (honors user override)."""
    return settings.project_directory


def is_projects_root_overridden() -> bool:
    """Check if a projects root override is configured."""
    return settings.get("project_directory") is not None
