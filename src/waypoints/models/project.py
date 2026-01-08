"""Project model for organizing waypoints work."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def slugify(name: str) -> str:
    """Convert a name to a URL-friendly slug.

    Examples:
        "AI Task Manager" -> "ai-task-manager"
        "My Project 2.0" -> "my-project-2-0"
    """
    # Convert to lowercase
    slug = name.lower()
    # Replace spaces and underscores with hyphens
    slug = re.sub(r"[\s_]+", "-", slug)
    # Remove non-alphanumeric characters (except hyphens)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    # Remove consecutive hyphens
    slug = re.sub(r"-+", "-", slug)
    # Strip leading/trailing hyphens
    slug = slug.strip("-")
    return slug or "unnamed-project"


@dataclass
class Project:
    """A waypoints project containing sessions and documents."""

    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
    initial_idea: str = ""

    @classmethod
    def create(cls, name: str, idea: str = "") -> "Project":
        """Create a new project with the given name."""
        slug = slugify(name)
        now = datetime.now()
        project = cls(
            name=name,
            slug=slug,
            created_at=now,
            updated_at=now,
            initial_idea=idea,
        )
        # Create directory structure and save metadata
        project._ensure_directories()
        project.save()
        return project

    def get_path(self) -> Path:
        """Get the project's root directory path."""
        return Path.cwd() / ".waypoints" / "projects" / self.slug

    def get_sessions_path(self) -> Path:
        """Get the sessions directory path."""
        return self.get_path() / "sessions"

    def get_docs_path(self) -> Path:
        """Get the docs directory path."""
        return self.get_path() / "docs"

    def _ensure_directories(self) -> None:
        """Create project directory structure."""
        self.get_path().mkdir(parents=True, exist_ok=True)
        self.get_sessions_path().mkdir(exist_ok=True)
        self.get_docs_path().mkdir(exist_ok=True)

    def save(self) -> None:
        """Save project metadata to project.json."""
        self.updated_at = datetime.now()
        self._ensure_directories()
        metadata_path = self.get_path() / "project.json"
        metadata_path.write_text(json.dumps(self.to_dict(), indent=2))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "slug": self.slug,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "initial_idea": self.initial_idea,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            slug=data["slug"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            initial_idea=data.get("initial_idea", ""),
        )

    @classmethod
    def load(cls, slug: str) -> "Project":
        """Load a project by its slug."""
        metadata_path = Path.cwd() / ".waypoints" / "projects" / slug / "project.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Project not found: {slug}")
        data = json.loads(metadata_path.read_text())
        return cls.from_dict(data)

    @classmethod
    def list_all(cls) -> list["Project"]:
        """List all projects in the current workspace."""
        projects_dir = Path.cwd() / ".waypoints" / "projects"
        if not projects_dir.exists():
            return []
        projects = []
        for project_dir in projects_dir.iterdir():
            if project_dir.is_dir():
                try:
                    projects.append(cls.load(project_dir.name))
                except (FileNotFoundError, json.JSONDecodeError):
                    pass  # Skip invalid projects
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)
