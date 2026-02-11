"""Git policy helpers for Fly phase commit/rollback operations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from waypoints.git.config import GitConfig
from waypoints.git.receipt import ReceiptValidator
from waypoints.git.service import GitService
from waypoints.models.flight_plan import FlightPlanReader
from waypoints.orchestration.types import CommitResult, RollbackResult

if TYPE_CHECKING:
    from waypoints.models.project import Project
    from waypoints.models.waypoint import Waypoint

logger = logging.getLogger(__name__)


def _prepare_git_repo(
    git: GitService, *, auto_init: bool
) -> tuple[bool, CommitResult | None]:
    """Ensure repository exists. Returns (initialized_repo, early_result)."""
    if git.is_git_repo():
        return (False, None)

    if not auto_init:
        return (
            False,
            CommitResult(
                committed=False,
                message="Not a git repo and auto-init disabled",
            ),
        )

    init_result = git.init_repo()
    if not init_result.success:
        return (
            False,
            CommitResult(
                committed=False,
                message=f"Failed to init git repo: {init_result.message}",
            ),
        )
    return (True, None)


def _validate_receipt(project: "Project", waypoint: "Waypoint") -> CommitResult | None:
    """Validate receipt for waypoint when checklist validation is enabled."""
    validator = ReceiptValidator()
    receipt_path = validator.find_latest_receipt(project, waypoint.id)
    if receipt_path is None:
        return CommitResult(
            committed=False,
            message=f"No receipt found for {waypoint.id}",
        )

    validation = validator.validate(receipt_path)
    if validation.valid:
        return None
    return CommitResult(
        committed=False,
        message=f"Receipt invalid: {validation.message}",
    )


def _commit_with_staging(
    git: GitService,
    *,
    slug: str,
    waypoint_title: str,
    initialized_repo: bool,
) -> CommitResult:
    """Stage project files and perform commit."""
    git.stage_project_files(slug)

    commit_msg = f"feat({slug}): Complete {waypoint_title}"
    result = git.commit(commit_msg)
    if result.success:
        return CommitResult(
            committed=True,
            message=commit_msg,
            commit_hash=git.get_head_commit(),
            initialized_repo=initialized_repo,
        )

    if "Nothing to commit" in result.message:
        return CommitResult(
            committed=False,
            message="Nothing to commit",
            initialized_repo=initialized_repo,
        )
    return CommitResult(
        committed=False,
        message=f"Commit failed: {result.message}",
        initialized_repo=initialized_repo,
    )


def commit_waypoint(
    project: "Project",
    waypoint: "Waypoint",
    *,
    git_config: GitConfig | None = None,
    git_service: GitService | None = None,
) -> CommitResult:
    """Commit waypoint changes to git using configured policy."""
    config = git_config or GitConfig.load(project.slug)
    project_path = project.get_path()

    if not config.auto_commit:
        return CommitResult(committed=False, message="Auto-commit disabled")

    git = git_service or GitService(project_path)

    initialized, init_failure = _prepare_git_repo(git, auto_init=config.auto_init)
    if init_failure is not None:
        return init_failure

    if config.run_checklist:
        receipt_failure = _validate_receipt(project, waypoint)
        if receipt_failure is not None:
            return receipt_failure

    slug = project.slug
    commit_result = _commit_with_staging(
        git,
        slug=slug,
        waypoint_title=waypoint.title,
        initialized_repo=initialized,
    )
    if not commit_result.committed:
        return commit_result

    tag_name = None
    if config.create_waypoint_tags:
        tag_name = f"{slug}/{waypoint.id}"
        git.tag(tag_name, f"Completed waypoint: {waypoint.title}")

    logger.info("Committed waypoint %s: %s", waypoint.id, commit_result.message)
    return CommitResult(
        committed=commit_result.committed,
        message=commit_result.message,
        commit_hash=commit_result.commit_hash,
        tag_name=tag_name,
        initialized_repo=initialized,
    )


def rollback_to_ref(
    project: "Project",
    ref: str | None,
    *,
    git_service: GitService | None = None,
) -> RollbackResult:
    """Rollback git to a reference (tag/HEAD/commit-ish) and reload the plan."""
    git = git_service or GitService(project.get_path())
    if not git.is_git_repo():
        return RollbackResult(success=False, message="Not a git repository")

    resolved_ref = ref.strip() if isinstance(ref, str) and ref.strip() else None
    head_commit = git.get_head_commit()
    if resolved_ref is None and head_commit is not None:
        resolved_ref = "HEAD"
    if resolved_ref is None:
        return RollbackResult(
            success=False,
            message=(
                "No rollback reference available. Create a rollback anchor with "
                '`git add -A && git commit -m "checkpoint: safe rollback anchor"`.'
            ),
        )

    result = git.reset_hard(resolved_ref)
    if not result.success:
        return RollbackResult(
            success=False,
            message=f"Rollback failed: {result.message}",
            resolved_ref=resolved_ref,
        )

    loaded = FlightPlanReader.load(project)
    resolved_display = (
        f"HEAD ({head_commit})"
        if resolved_ref == "HEAD" and head_commit is not None
        else resolved_ref
    )
    logger.info("Rolled back to %s", resolved_display)
    return RollbackResult(
        success=True,
        message=f"Rolled back to {resolved_display}",
        resolved_ref=resolved_ref,
        flight_plan=loaded,
    )


def rollback_to_tag(
    project: "Project",
    tag: str | None,
    *,
    git_service: GitService | None = None,
) -> RollbackResult:
    """Compatibility wrapper for legacy rollback tag naming."""
    return rollback_to_ref(project, tag, git_service=git_service)
