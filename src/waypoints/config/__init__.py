"""Configuration management for Waypoints."""
from __future__ import annotations

from waypoints.config.paths import WaypointsPaths, get_paths, reset_paths
from waypoints.config.settings import Settings, get_settings_path, settings

__all__ = [
    "Settings",
    "WaypointsPaths",
    "get_paths",
    "get_settings_path",
    "reset_paths",
    "settings",
]
