"""Project model for organizing waypoints work."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoints.config.project_root import get_projects_root

if TYPE_CHECKING:
    from waypoints.models.journey import Journey, JourneyState


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
    summary: str = ""  # LLM-generated project summary
    journey: Journey | None = field(default=None, repr=False)

    @classmethod
    def create(cls, name: str, idea: str = "") -> "Project":
        """Create a new project with the given name."""
        from waypoints.models.journey import Journey

        slug = slugify(name)
        now = datetime.now(UTC)
        project = cls(
            name=name,
            slug=slug,
            created_at=now,
            updated_at=now,
            initial_idea=idea,
            journey=Journey.new(slug),
        )
        # Create directory structure and save metadata
        project._ensure_directories()
        project.save()
        return project

    @classmethod
    def _get_projects_dir(cls) -> Path:
        """Get the projects directory from settings (respects user override)."""
        return get_projects_root()

    def get_path(self) -> Path:
        """Get the project's root directory path."""
        return self._get_projects_dir() / self.slug

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
        self.updated_at = datetime.now(UTC)
        self._ensure_directories()
        metadata_path = self.get_path() / "project.json"
        metadata_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        data: dict[str, Any] = {
            "name": self.name,
            "slug": self.slug,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "initial_idea": self.initial_idea,
            "summary": self.summary,
        }
        if self.journey is not None:
            data["journey"] = self.journey.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        """Create from dictionary."""
        from waypoints.models.journey import Journey

        journey = None
        if "journey" in data:
            journey = Journey.from_dict(data["journey"])

        return cls(
            name=data["name"],
            slug=data["slug"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            initial_idea=data.get("initial_idea", ""),
            summary=data.get("summary", ""),
            journey=journey,
        )

    @classmethod
    def load(cls, slug: str) -> "Project":
        """Load a project by its slug."""
        metadata_path = cls._get_projects_dir() / slug / "project.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Project not found: {slug}")
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def list_all(cls) -> list["Project"]:
        """List all projects in the configured projects directory."""
        projects_dir = cls._get_projects_dir()
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

    def transition_journey(
        self,
        target: JourneyState,
        reason: str | None = None,
    ) -> None:
        """Transition the journey to a new state and save.

        If the project doesn't have a journey yet, one will be created.
        If already in the target state, this is a no-op (idempotent).

        Args:
            target: The state to transition to.

        Raises:
            InvalidTransitionError: If the transition is not valid.
        """
        from waypoints.models.journey import Journey

        if self.journey is None:
            self.journey = Journey.new(self.slug)

        # Idempotent: if already in target state, nothing to do
        if self.journey.state == target:
            return

        self.journey = self.journey.transition(target, reason=reason)
        self.save()

        # Commit at milestone states (domain layer, not UX layer)
        self._commit_milestone_if_needed(target)

    def _generate_release_notes(self) -> None:
        """Generate release notes from product spec and completed waypoints.

        Creates a docs/release-notes.md file with project summary
        and key features extracted from the product specification.
        """
        import logging
        from datetime import UTC, datetime

        from waypoints.models.flight_plan import FlightPlanReader
        from waypoints.models.waypoint import WaypointStatus

        logger = logging.getLogger(__name__)

        flight_plan = FlightPlanReader.load(self)
        if not flight_plan:
            logger.debug("No flight plan found, skipping release notes")
            return

        # Get completed top-level waypoints (not subtasks)
        completed = [
            wp
            for wp in flight_plan.waypoints
            if wp.status == WaypointStatus.COMPLETE and not wp.parent_id
        ]

        if not completed:
            logger.debug("No completed waypoints, skipping release notes")
            return

        # Generate release notes content
        lines = [
            f"# {self.name} - Release Notes",
            "",
        ]

        # Add summary if available
        summary = self.summary or self.initial_idea or ""
        if summary:
            # Truncate long summaries
            if len(summary) > 800:
                summary = summary[:800] + "..."
            lines.extend([summary, ""])

        # Extract features from product spec
        features = self._extract_features_from_spec()
        if features:
            lines.extend(["## Key Features", ""])
            lines.extend(features)
            lines.append("")

        lines.extend(
            [
                "## Build Info",
                "",
                f"- Waypoints completed: {len(completed)}",
                f"- Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            ]
        )

        # Save to docs directory
        docs_dir = self.get_docs_path()
        docs_dir.mkdir(parents=True, exist_ok=True)
        release_notes_path = docs_dir / "release-notes.md"
        release_notes_path.write_text("\n".join(lines))
        logger.info("Generated release notes: %s", release_notes_path)

    def _extract_features_from_spec(self) -> list[str]:
        """Extract key features from the product specification.

        Looks for the Features section in product-spec.md and extracts
        feature titles and descriptions.

        Returns:
            List of markdown-formatted feature lines.
        """
        import re

        docs_dir = self.get_docs_path()

        # Find the latest product spec file
        spec_files = sorted(docs_dir.glob("product-spec*.md"), reverse=True)
        if not spec_files:
            return []

        spec_content = spec_files[0].read_text(encoding="utf-8")

        # Look for Features section (various heading formats)
        # Match: ## Features, ## 5. Features, ## Key Features, etc.
        features_match = re.search(
            r"^##\s+(?:\d+\.\s+)?(?:Key\s+)?Features.*?$\n(.*?)(?=^##\s|\Z)",
            spec_content,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )

        if not features_match:
            return []

        features_section = features_match.group(1)

        # Extract feature titles from ### or #### headings
        # Match: ### 5.1 MVP Features, #### 5.1.1 Feature Name, etc.
        feature_titles = re.findall(
            r"^#{3,4}\s+(?:\d+(?:\.\d+)*\s+)?(.+?)$",
            features_section,
            re.MULTILINE,
        )

        # Format as bullet points, skip generic headings
        result = []
        skip_patterns = ["mvp", "must have", "nice to have", "requirements", "future"]
        for title in feature_titles:
            title_lower = title.lower()
            if not any(skip in title_lower for skip in skip_patterns):
                result.append(f"- **{title.strip()}**")

        return result[:10]  # Limit to top 10 features

    def _commit_milestone_if_needed(self, state: JourneyState) -> None:
        """Commit project artifacts when reaching a milestone state.

        This is called from transition_journey() to ensure commits only
        happen on actual state transitions, not screen navigation.
        """
        import logging
        from datetime import UTC, datetime

        from waypoints.git.config import GitConfig
        from waypoints.git.service import GitService
        from waypoints.models.journey import JourneyState

        logger = logging.getLogger(__name__)

        # Map milestone states to (commit_message, phase_name)
        milestone_commits = {
            JourneyState.SHAPE_BRIEF_REVIEW: (
                "feat({slug}): Complete ideation",
                "idea-brief",
            ),
            JourneyState.SHAPE_SPEC_REVIEW: (
                "feat({slug}): Finalize idea brief",
                "product-spec",
            ),
            JourneyState.CHART_REVIEW: (
                "feat({slug}): Complete product spec",
                "chart",
            ),
            JourneyState.FLY_READY: (
                "feat({slug}): Flight plan ready",
                "fly",
            ),
            JourneyState.LAND_REVIEW: (
                "feat({slug}): Complete all waypoints",
                "land",
            ),
        }

        if state not in milestone_commits:
            return

        # Generate release notes at landing (before staging)
        if state == JourneyState.LAND_REVIEW:
            self._generate_release_notes()

        config = GitConfig.load(self.slug)
        if not config.auto_commit:
            logger.debug("Auto-commit disabled, skipping milestone commit")
            return

        message_template, phase_name = milestone_commits[state]
        git = GitService(self.get_path())

        # Auto-init if needed
        if not git.is_git_repo():
            if config.auto_init:
                result = git.init_repo()
                if not result.success:
                    logger.warning("Failed to init git repo: %s", result.message)
                    return
            else:
                logger.debug("Not a git repo and auto-init disabled")
                return

        # Stage all project files
        git.stage_project_files(self.slug)

        # Commit
        commit_msg = message_template.format(slug=self.slug)
        result = git.commit(commit_msg)

        if result.success and "Nothing to commit" not in result.message:
            logger.info("Milestone commit: %s", commit_msg)

            # Tag with phase name and timestamp
            if config.create_phase_tags:
                timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                tag_name = f"{self.slug}/{phase_name}-{timestamp}"
                tag_result = git.tag(tag_name, f"Phase: {phase_name}")
                if tag_result.success:
                    logger.info("Created tag: %s", tag_name)
        elif not result.success:
            logger.error("Milestone commit failed: %s", result.message)

    def delete(self) -> None:
        """Delete this project and all its files.

        Removes the entire project directory including:
        - project.json
        - sessions/
        - docs/
        - flight-plan.jsonl
        """
        project_path = self.get_path()
        if project_path.exists():
            shutil.rmtree(project_path)
