"""Git configuration for waypoints.

Configuration is hierarchical:
1. Global defaults (~/.waypoints/git-config.json)
2. Workspace config (.waypoints/git-config.json)

Checklists are per-project artifacts:
- .waypoints/projects/{slug}/checklist.yaml
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

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
    def load(cls, project_path: Path | None = None) -> "GitConfig":
        """Load config from project, workspace, or global settings.

        Args:
            project_path: Path to project directory. If provided, checks for
                project-specific config first.
        """
        # Try project-specific config first
        if project_path:
            project_config = project_path / ".waypoints" / "git-config.json"
            if project_config.exists():
                return cls._from_file(project_config)

        # Try workspace config
        if project_path:
            workspace_path = project_path / ".waypoints" / "git-config.json"
        else:
            workspace_path = Path.cwd() / ".waypoints" / "git-config.json"
        if workspace_path.exists():
            return cls._from_file(workspace_path)

        # Fall back to global
        global_path = Path.home() / ".waypoints" / "git-config.json"
        if global_path.exists():
            return cls._from_file(global_path)

        # Return defaults
        logger.debug("Using default git config")
        return cls()

    @classmethod
    def _from_file(cls, path: Path) -> "GitConfig":
        """Load from JSON file."""
        try:
            data = json.loads(path.read_text())
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
        """Save configuration."""
        if workspace:
            path = Path.cwd() / ".waypoints" / "git-config.json"
        else:
            path = Path.home() / ".waypoints" / "git-config.json"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._to_dict(), indent=2))
        logger.info("Saved git config to %s", path)

    def _to_dict(self) -> dict:
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

    Stored as a project artifact at .waypoints/projects/{slug}/checklist.yaml
    The model interprets these conceptually and produces receipts.
    """

    items: list[str] = field(default_factory=lambda: list(DEFAULT_CHECKLIST))

    @classmethod
    def load(cls, project_path: Path) -> "Checklist":
        """Load checklist from project directory.

        Args:
            project_path: Path to .waypoints/projects/{slug}/
        """
        checklist_path = project_path / "checklist.yaml"

        if not checklist_path.exists():
            # Create default checklist as project artifact
            checklist = cls()
            checklist.save(project_path)
            return checklist

        try:
            data = yaml.safe_load(checklist_path.read_text())
            items = data.get("checklist", DEFAULT_CHECKLIST)
            logger.debug(
                "Loaded checklist from %s: %d items", checklist_path, len(items)
            )
            return cls(items=items)
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to load checklist from %s: %s", checklist_path, e)
            return cls()

    def save(self, project_path: Path) -> None:
        """Save checklist to project directory."""
        checklist_path = project_path / "checklist.yaml"
        checklist_path.parent.mkdir(parents=True, exist_ok=True)

        content = yaml.dump(
            {"checklist": self.items},
            default_flow_style=False,
            sort_keys=False,
        )
        checklist_path.write_text(content)
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
