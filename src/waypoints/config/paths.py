"""Centralized path management for Waypoints.

Follows XDG Base Directory Specification:
- Config: $XDG_CONFIG_HOME/waypoints (default: ~/.config/waypoints)
- Data: $XDG_DATA_HOME/waypoints (default: ~/.local/share/waypoints)
- State: $XDG_STATE_HOME/waypoints (default: ~/.local/state/waypoints)
- Cache: $XDG_CACHE_HOME/waypoints (default: ~/.cache/waypoints)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _xdg_config_home() -> Path:
    """Get XDG_CONFIG_HOME, defaulting to ~/.config."""
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _xdg_data_home() -> Path:
    """Get XDG_DATA_HOME, defaulting to ~/.local/share."""
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def _xdg_state_home() -> Path:
    """Get XDG_STATE_HOME, defaulting to ~/.local/state."""
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def _xdg_cache_home() -> Path:
    """Get XDG_CACHE_HOME, defaulting to ~/.cache."""
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


@dataclass
class WaypointsPaths:
    """Centralized path management following XDG spec."""

    workspace: Path  # Current working directory

    # XDG directories (computed once at init)
    _config_home: Path = field(default_factory=_xdg_config_home)
    _data_home: Path = field(default_factory=_xdg_data_home)
    _state_home: Path = field(default_factory=_xdg_state_home)
    _cache_home: Path = field(default_factory=_xdg_cache_home)

    # === WORKSPACE PATHS (project-local) ===

    @property
    def workspace_config(self) -> Path:
        """Workspace .waypoints/ directory."""
        return self.workspace / ".waypoints"

    @property
    def projects_dir(self) -> Path:
        """Workspace projects directory."""
        return self.workspace_config / "projects"

    # === PROJECT PATHS ===

    def project_dir(self, slug: str) -> Path:
        """Get the project directory for a given slug."""
        return self.projects_dir / slug

    def project_file(self, slug: str) -> Path:
        """Get the project.json file path for a given slug."""
        return self.project_dir(slug) / "project.json"

    def sessions_dir(self, slug: str) -> Path:
        """Get the sessions directory for a given slug."""
        return self.project_dir(slug) / "sessions"

    def execution_logs_dir(self, slug: str) -> Path:
        """Get the execution logs (fly) directory for a given slug."""
        return self.sessions_dir(slug) / "fly"

    def docs_dir(self, slug: str) -> Path:
        """Get the docs directory for a given slug."""
        return self.project_dir(slug) / "docs"

    def flight_plan(self, slug: str) -> Path:
        """Get the flight-plan.jsonl file path for a given slug."""
        return self.project_dir(slug) / "flight-plan.jsonl"

    def metrics(self, slug: str) -> Path:
        """Get the metrics.jsonl file path for a given slug."""
        return self.project_dir(slug) / "metrics.jsonl"

    def receipts_dir(self, slug: str) -> Path:
        """Get the receipts directory for a given slug."""
        return self.project_dir(slug) / "receipts"

    def checklist(self, slug: str) -> Path:
        """Get the checklist.yaml file path for a given slug."""
        return self.project_dir(slug) / "checklist.yaml"

    # === GLOBAL PATHS (XDG compliant) ===

    @property
    def global_config_dir(self) -> Path:
        """Global config: ~/.config/waypoints/"""
        return self._config_home / "waypoints"

    @property
    def global_settings(self) -> Path:
        """Global settings file: ~/.config/waypoints/settings.json"""
        return self.global_config_dir / "settings.json"

    @property
    def global_git_config(self) -> Path:
        """Global git config: ~/.config/waypoints/git-config.json"""
        return self.global_config_dir / "git-config.json"

    @property
    def global_data_dir(self) -> Path:
        """Global data: ~/.local/share/waypoints/"""
        return self._data_home / "waypoints"

    @property
    def global_state_dir(self) -> Path:
        """Global state: ~/.local/state/waypoints/"""
        return self._state_home / "waypoints"

    @property
    def global_cache_dir(self) -> Path:
        """Global cache: ~/.cache/waypoints/"""
        return self._cache_home / "waypoints"

    # === WORKSPACE-LEVEL CONFIG ===

    @property
    def workspace_git_config(self) -> Path:
        """Workspace git config: .waypoints/git-config.json"""
        return self.workspace_config / "git-config.json"

    @property
    def debug_log(self) -> Path:
        """Debug log: .waypoints/debug.log"""
        return self.workspace_config / "debug.log"

    # === CONFIG RESOLUTION ===

    def git_config(self, slug: str | None = None) -> Path | None:
        """Resolve git config: project > workspace > global.

        Returns the first existing config file in the hierarchy,
        or None if no config exists.
        """
        if slug:
            project_config = self.project_dir(slug) / "git-config.json"
            if project_config.exists():
                return project_config

        if self.workspace_git_config.exists():
            return self.workspace_git_config

        if self.global_git_config.exists():
            return self.global_git_config

        return None

    # === DIRECTORY CREATION ===

    def ensure_project_dirs(self, slug: str) -> None:
        """Create all directories for a project."""
        self.project_dir(slug).mkdir(parents=True, exist_ok=True)
        self.sessions_dir(slug).mkdir(exist_ok=True)
        self.execution_logs_dir(slug).mkdir(exist_ok=True)
        self.docs_dir(slug).mkdir(exist_ok=True)
        self.receipts_dir(slug).mkdir(exist_ok=True)

    def ensure_global_dirs(self) -> None:
        """Create global XDG directories."""
        self.global_config_dir.mkdir(parents=True, exist_ok=True)
        self.global_data_dir.mkdir(parents=True, exist_ok=True)
        self.global_state_dir.mkdir(parents=True, exist_ok=True)
        self.global_cache_dir.mkdir(parents=True, exist_ok=True)


# Singleton instance
_paths: WaypointsPaths | None = None


def get_paths(workspace: Path | None = None) -> WaypointsPaths:
    """Get the paths singleton.

    On first call, optionally set the workspace directory.
    Subsequent calls return the same instance.

    Args:
        workspace: The workspace directory. If not provided on first call,
                   defaults to current working directory.

    Returns:
        The WaypointsPaths singleton instance.
    """
    global _paths
    if _paths is None:
        _paths = WaypointsPaths(workspace=workspace or Path.cwd())
    return _paths


def reset_paths() -> None:
    """Reset paths singleton (for testing)."""
    global _paths
    _paths = None
