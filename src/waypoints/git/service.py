"""Git operations service for waypoints.

Handles git init, staging, commits, and tags. This is the "dog" layer -
it executes git commands but relies on receipt validation to decide
whether to proceed.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Default .gitignore content for waypoints projects
DEFAULT_GITIGNORE = """\
# Waypoints application logs (not project artifacts)
.waypoints/debug.log
.waypoints/**/debug.log

# User settings (not project-specific)
.waypoints/settings.json

# Build artifacts
target/
build/
dist/
out/
*.egg-info/

# Dependencies
node_modules/
vendor/

# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
.env
.coverage
.pytest_cache/
.ruff_cache/
.mypy_cache/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db
"""


@dataclass
class GitResult:
    """Result of a git operation."""

    success: bool
    message: str
    output: str = ""


class GitService:
    """Handles all git operations for waypoints."""

    def __init__(self, working_dir: Path) -> None:
        """Initialize git service.

        Args:
            working_dir: Directory to run git commands in. Required.
        """
        self.working_dir = working_dir

    def _run_git(
        self, *args: str, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command."""
        cmd = ["git", *args]
        logger.debug("Running: %s", " ".join(cmd))
        return subprocess.run(
            cmd,
            cwd=self.working_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def is_git_repo(self) -> bool:
        """Check if current directory is inside a git repository."""
        result = self._run_git("rev-parse", "--git-dir")
        return result.returncode == 0

    def init_repo(self) -> GitResult:
        """Initialize a new git repository.

        Also creates a default .gitignore if one doesn't exist.
        """
        if self.is_git_repo():
            return GitResult(True, "Already a git repository")

        try:
            result = self._run_git("init")
            if result.returncode != 0:
                return GitResult(False, f"Failed to initialize: {result.stderr}")

            # Create default .gitignore
            self._create_gitignore()

            logger.info("Initialized git repository at %s", self.working_dir)
            return GitResult(True, "Initialized git repository", result.stdout)
        except Exception as e:
            logger.error("Git init error: %s", e)
            return GitResult(False, f"Git error: {e}")

    def _create_gitignore(self) -> None:
        """Create default .gitignore with waypoints app logs excluded."""
        gitignore_path = self.working_dir / ".gitignore"
        if gitignore_path.exists():
            # Append our entries if not already present
            content = gitignore_path.read_text()
            if ".waypoints/debug.log" not in content:
                gitignore_path.write_text(content + "\n" + DEFAULT_GITIGNORE)
                logger.info("Appended waypoints entries to .gitignore")
        else:
            gitignore_path.write_text(DEFAULT_GITIGNORE)
            logger.info("Created .gitignore")

    def stage_files(self, *patterns: str) -> GitResult:
        """Stage files matching the given patterns.

        Args:
            patterns: Glob patterns or file paths to stage.
        """
        if not patterns:
            return GitResult(False, "No patterns specified")

        try:
            for pattern in patterns:
                self._run_git("add", pattern)
            return GitResult(True, f"Staged {len(patterns)} pattern(s)")
        except Exception as e:
            logger.error("Staging error: %s", e)
            return GitResult(False, f"Staging error: {e}")

    def stage_project_files(self, project_slug: str) -> GitResult:
        """Stage all project artifacts for commit.

        Stages all files in the project directory, including:
        - Waypoints metadata (docs/, project.json, flight-plan.jsonl, etc.)
        - Generated source code (src/, tests/, etc.)
        - Receipts (.waypoints/projects/{slug}/receipts/)

        Respects .gitignore for excluding unwanted files.
        """
        # Stage all files in the working directory (respects .gitignore)
        return self.stage_files(".")

    def has_staged_changes(self) -> bool:
        """Check if there are staged changes ready to commit."""
        result = self._run_git("diff", "--cached", "--quiet")
        return result.returncode != 0

    def has_uncommitted_changes(self, path: str | None = None) -> bool:
        """Check if there are uncommitted changes."""
        args = ["status", "--porcelain"]
        if path:
            args.append(path)
        result = self._run_git(*args)
        return bool(result.stdout.strip())

    def commit(self, message: str) -> GitResult:
        """Create a commit with the given message.

        Args:
            message: Commit message.
        """
        if not self.has_staged_changes():
            return GitResult(True, "Nothing to commit")

        try:
            result = self._run_git("commit", "-m", message)
            if result.returncode == 0:
                logger.info("Created commit: %s", message[:50])
                return GitResult(True, "Commit created", result.stdout)

            # Check for common issues
            combined = result.stdout + result.stderr
            if "nothing to commit" in combined:
                return GitResult(True, "Nothing to commit")

            logger.error("Commit failed: %s", result.stderr)
            return GitResult(False, f"Commit failed: {result.stderr}")
        except Exception as e:
            logger.error("Commit error: %s", e)
            return GitResult(False, f"Commit error: {e}")

    def tag(self, name: str, message: str | None = None) -> GitResult:
        """Create an annotated tag.

        Args:
            name: Tag name (e.g., "myproject/v1.0.0")
            message: Tag message. Defaults to tag name if not provided.
        """
        tag_message = message or name

        try:
            # Check if tag already exists
            check = self._run_git("tag", "-l", name)
            if name in check.stdout:
                logger.warning("Tag already exists: %s", name)
                return GitResult(True, f"Tag already exists: {name}")

            result = self._run_git("tag", "-a", name, "-m", tag_message)
            if result.returncode == 0:
                logger.info("Created tag: %s", name)
                return GitResult(True, f"Created tag: {name}")

            logger.error("Tag failed: %s", result.stderr)
            return GitResult(False, f"Tag failed: {result.stderr}")
        except Exception as e:
            logger.error("Tag error: %s", e)
            return GitResult(False, f"Tag error: {e}")

    def get_current_branch(self) -> str | None:
        """Get the name of the current branch."""
        result = self._run_git("branch", "--show-current")
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None

    def get_head_commit(self) -> str | None:
        """Get the short hash of HEAD commit."""
        result = self._run_git("rev-parse", "--short", "HEAD")
        if result.returncode == 0:
            return result.stdout.strip()
        return None

    def reset_hard(self, target: str) -> GitResult:
        """Reset the working directory to a specific commit or tag.

        WARNING: This is a destructive operation that discards uncommitted changes.

        Args:
            target: Commit hash, tag name, or branch to reset to.

        Returns:
            GitResult indicating success or failure.
        """
        try:
            # Verify the target exists
            check = self._run_git("rev-parse", "--verify", target)
            if check.returncode != 0:
                return GitResult(False, f"Target not found: {target}")

            result = self._run_git("reset", "--hard", target)
            if result.returncode == 0:
                logger.info("Reset to: %s", target)
                return GitResult(True, f"Reset to {target}", result.stdout)

            logger.error("Reset failed: %s", result.stderr)
            return GitResult(False, f"Reset failed: {result.stderr}")
        except Exception as e:
            logger.error("Reset error: %s", e)
            return GitResult(False, f"Reset error: {e}")
