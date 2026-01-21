"""Git configuration for waypoints.

Configuration is hierarchical:
1. Global defaults (~/.config/waypoints/git-config.json)
2. Workspace config (.waypoints/git-config.json)

Checklists are per-project artifacts:
- {project_dir}/checklist.yaml
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from waypoints.config.paths import get_paths

if TYPE_CHECKING:
    from waypoints.models.project import Project

logger = logging.getLogger(__name__)

# Default conceptual checklist items
DEFAULT_CHECKLIST = [
    "Code passes linting",
    "All tests pass",
    "No type errors",
    "Code is properly formatted",
]


@dataclass
class GitConfig:
    """Git configuration settings."""

    # Core settings
    auto_commit: bool = True  # Auto-commit on phase transitions
    auto_init: bool = True  # Auto-init git repo if missing
    run_checklist: bool = True  # Require checklist receipt before commit

    # Tag settings
    create_phase_tags: bool = True  # Create tags at phase boundaries
    create_waypoint_tags: bool = False  # Create tags per waypoint completion

    @classmethod
    def load(cls, slug: str | None = None) -> "GitConfig":
        """Load config from project, workspace, or global settings.

        Resolution order: project > workspace > global.

        Args:
            slug: Project slug. If provided, checks for project-specific config first.
        """
        paths = get_paths()

        # Use centralized resolution: project > workspace > global
        config_path = paths.git_config(slug)
        if config_path:
            return cls._from_file(config_path)

        # Return defaults
        logger.debug("Using default git config")
        return cls()

    @classmethod
    def _from_file(cls, path: Path) -> "GitConfig":
        """Load from JSON file."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.debug("Loaded git config from %s", path)
            return cls(
                auto_commit=data.get("auto_commit", True),
                auto_init=data.get("auto_init", True),
                run_checklist=data.get("run_checklist", True),
                create_phase_tags=data.get("create_phase_tags", True),
                create_waypoint_tags=data.get("create_waypoint_tags", False),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load git config from %s: %s", path, e)
            return cls()

    def save(self, workspace: bool = True) -> None:
        """Save configuration.

        Args:
            workspace: If True, save to workspace config. If False, save to global.
        """
        paths = get_paths()
        if workspace:
            path = paths.workspace_git_config
        else:
            path = paths.global_git_config

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._to_dict(), indent=2), encoding="utf-8")
        logger.info("Saved git config to %s", path)

    def _to_dict(self) -> dict[str, bool]:
        """Convert to dictionary."""
        return {
            "auto_commit": self.auto_commit,
            "auto_init": self.auto_init,
            "run_checklist": self.run_checklist,
            "create_phase_tags": self.create_phase_tags,
            "create_waypoint_tags": self.create_waypoint_tags,
        }


@dataclass
class Checklist:
    """Conceptual checklist for a project.

    Stored as a project artifact at {project_dir}/checklist.yaml
    The model interprets these conceptually and produces receipts.

    Supports optional validation command overrides for stack-specific tools.
    """

    items: list[str] = field(default_factory=lambda: list(DEFAULT_CHECKLIST))
    # Override commands by category: {"lint": "uv run ruff check .", "test": "..."}
    validation_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, project: "Project") -> "Checklist":
        """Load checklist from project directory.

        Args:
            project: The project to load checklist for.
        """
        checklist_path = project.get_path() / "checklist.yaml"

        if not checklist_path.exists():
            # Create default checklist as project artifact
            checklist = cls()
            checklist.save(project)
            return checklist

        try:
            data = yaml.safe_load(checklist_path.read_text(encoding="utf-8"))
            items = data.get("checklist", DEFAULT_CHECKLIST)

            # Parse validation command overrides
            validation = data.get("validation", {})
            overrides = validation.get("commands", {})

            logger.debug(
                "Loaded checklist from %s: %d items, %d overrides",
                checklist_path,
                len(items),
                len(overrides),
            )
            return cls(items=items, validation_overrides=overrides)
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to load checklist from %s: %s", checklist_path, e)
            return cls()

    def save(self, project: "Project") -> None:
        """Save checklist to project directory."""
        checklist_path = project.get_path() / "checklist.yaml"
        checklist_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, object] = {"checklist": self.items}

        # Include validation overrides if present
        if self.validation_overrides:
            data["validation"] = {"commands": self.validation_overrides}

        content = yaml.dump(data, default_flow_style=False, sort_keys=False)
        checklist_path.write_text(content, encoding="utf-8")
        logger.info("Saved checklist to %s", checklist_path)

    def to_prompt(self) -> str:
        """Format checklist for inclusion in model prompts."""
        items_str = "\n".join(f"- {item}" for item in self.items)
        return f"""Before marking this waypoint complete, verify the following:

{items_str}

After completing your work, produce a checklist receipt as a JSON file at:
.waypoints/projects/{{project_slug}}/receipts/{{waypoint_id}}-{{timestamp}}.json

The receipt should contain:
- waypoint_id: The ID of the waypoint
- completed_at: ISO timestamp
- checklist: Array of items with status, evidence, and reason (if skipped)
"""
