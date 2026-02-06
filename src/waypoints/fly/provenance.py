"""Workspace provenance tracking for waypoint execution.

Captures before/after workspace snapshots and computes a file-level diff
summary that acts as:
- provenance: what files changed during a waypoint run
- rough token proxy: approximate token volume from textual deltas
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

ChangeType = Literal["added", "modified", "deleted"]

_IGNORED_DIRS: Final[set[str]] = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "sessions",
    "receipts",
}

_IGNORED_FILES: Final[set[str]] = {
    "metrics.jsonl",
}

_MAX_INLINE_TEXT_BYTES: Final[int] = 256 * 1024
_TEXT_SNIFF_BYTES: Final[int] = 4096
_MAX_CHANGED_FILES_REPORTED: Final[int] = 200
_TOP_CHANGED_FILES_REPORTED: Final[int] = 10


@dataclass(frozen=True)
class FileSnapshot:
    """Single file snapshot used for before/after diffing."""

    size_bytes: int
    digest: str
    is_text: bool
    content: str | None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Workspace snapshot captured at a point in time."""

    captured_at: datetime
    files: dict[str, FileSnapshot]


@dataclass(frozen=True)
class ChangedFile:
    """Per-file provenance record."""

    path: str
    change_type: ChangeType
    before_bytes: int | None
    after_bytes: int | None
    is_text: bool
    text_chars_added: int
    text_chars_removed: int

    @property
    def impact_chars(self) -> int:
        """Text impact magnitude for ranking changed files."""
        return self.text_chars_added + self.text_chars_removed

    def to_dict(self) -> dict[str, str | int | bool | None]:
        """Serialize for JSON logging."""
        return {
            "path": self.path,
            "change_type": self.change_type,
            "before_bytes": self.before_bytes,
            "after_bytes": self.after_bytes,
            "is_text": self.is_text,
            "text_chars_added": self.text_chars_added,
            "text_chars_removed": self.text_chars_removed,
        }


@dataclass(frozen=True)
class WorkspaceDiffSummary:
    """Aggregate provenance summary for one waypoint execution."""

    files_added: int
    files_modified: int
    files_deleted: int
    total_files_changed: int
    text_files_changed: int
    binary_files_changed: int
    text_chars_added: int
    text_chars_removed: int
    indeterminate_text_files: int
    net_bytes_delta: int
    approx_tokens_changed: int
    changed_files: list[ChangedFile]
    top_changed_files: list[ChangedFile]
    omitted_changed_files: int

    def to_dict(self) -> dict[str, object]:
        """Serialize for JSON logging."""
        return {
            "files_added": self.files_added,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
            "total_files_changed": self.total_files_changed,
            "text_files_changed": self.text_files_changed,
            "binary_files_changed": self.binary_files_changed,
            "text_chars_added": self.text_chars_added,
            "text_chars_removed": self.text_chars_removed,
            "indeterminate_text_files": self.indeterminate_text_files,
            "net_bytes_delta": self.net_bytes_delta,
            "approx_tokens_changed": self.approx_tokens_changed,
            "changed_files": [item.to_dict() for item in self.changed_files],
            "top_changed_files": [item.to_dict() for item in self.top_changed_files],
            "omitted_changed_files": self.omitted_changed_files,
        }


def capture_workspace_snapshot(project_path: Path) -> WorkspaceSnapshot:
    """Capture a lightweight snapshot of workspace files."""
    files: dict[str, FileSnapshot] = {}

    for file_path in _iter_workspace_files(project_path):
        rel_path = file_path.relative_to(project_path).as_posix()
        snapshot = _snapshot_file(file_path)
        if snapshot is not None:
            files[rel_path] = snapshot

    return WorkspaceSnapshot(captured_at=datetime.now(UTC), files=files)


def summarize_workspace_diff(
    before: WorkspaceSnapshot,
    after: WorkspaceSnapshot,
) -> WorkspaceDiffSummary:
    """Compute before/after workspace delta for provenance tracking."""
    before_paths = set(before.files)
    after_paths = set(after.files)

    added_paths = sorted(after_paths - before_paths)
    deleted_paths = sorted(before_paths - after_paths)
    maybe_modified_paths = sorted(before_paths & after_paths)

    changed_files: list[ChangedFile] = []
    text_chars_added = 0
    text_chars_removed = 0
    net_bytes_delta = 0
    indeterminate_text_files = 0

    for path in added_paths:
        after_state = after.files[path]
        added_chars = _estimate_text_size(after_state) if after_state.is_text else 0
        changed_files.append(
            ChangedFile(
                path=path,
                change_type="added",
                before_bytes=None,
                after_bytes=after_state.size_bytes,
                is_text=after_state.is_text,
                text_chars_added=added_chars,
                text_chars_removed=0,
            )
        )
        text_chars_added += added_chars
        net_bytes_delta += after_state.size_bytes

    for path in deleted_paths:
        before_state = before.files[path]
        removed_chars = _estimate_text_size(before_state) if before_state.is_text else 0
        changed_files.append(
            ChangedFile(
                path=path,
                change_type="deleted",
                before_bytes=before_state.size_bytes,
                after_bytes=None,
                is_text=before_state.is_text,
                text_chars_added=0,
                text_chars_removed=removed_chars,
            )
        )
        text_chars_removed += removed_chars
        net_bytes_delta -= before_state.size_bytes

    for path in maybe_modified_paths:
        before_state = before.files[path]
        after_state = after.files[path]
        if before_state.digest == after_state.digest:
            continue

        added_chars = 0
        removed_chars = 0
        is_text_change = before_state.is_text and after_state.is_text

        if is_text_change:
            if before_state.content is not None and after_state.content is not None:
                added_chars, removed_chars = _diff_text(
                    before_state.content, after_state.content
                )
            else:
                delta = after_state.size_bytes - before_state.size_bytes
                if delta > 0:
                    added_chars = delta
                elif delta < 0:
                    removed_chars = -delta
                else:
                    indeterminate_text_files += 1

        changed_files.append(
            ChangedFile(
                path=path,
                change_type="modified",
                before_bytes=before_state.size_bytes,
                after_bytes=after_state.size_bytes,
                is_text=is_text_change,
                text_chars_added=added_chars,
                text_chars_removed=removed_chars,
            )
        )
        text_chars_added += added_chars
        text_chars_removed += removed_chars
        net_bytes_delta += after_state.size_bytes - before_state.size_bytes

    total_files_changed = len(changed_files)
    text_files_changed = sum(1 for item in changed_files if item.is_text)
    binary_files_changed = total_files_changed - text_files_changed
    approx_tokens_changed = _approximate_tokens(text_chars_added + text_chars_removed)

    top_changed_files = sorted(
        changed_files,
        key=lambda item: (
            item.impact_chars,
            abs((item.after_bytes or 0) - (item.before_bytes or 0)),
            item.path,
        ),
        reverse=True,
    )[:_TOP_CHANGED_FILES_REPORTED]

    kept_changed_files = changed_files[:_MAX_CHANGED_FILES_REPORTED]
    omitted_changed_files = max(0, total_files_changed - len(kept_changed_files))

    return WorkspaceDiffSummary(
        files_added=len(added_paths),
        files_modified=sum(
            1 for item in changed_files if item.change_type == "modified"
        ),
        files_deleted=len(deleted_paths),
        total_files_changed=total_files_changed,
        text_files_changed=text_files_changed,
        binary_files_changed=binary_files_changed,
        text_chars_added=text_chars_added,
        text_chars_removed=text_chars_removed,
        indeterminate_text_files=indeterminate_text_files,
        net_bytes_delta=net_bytes_delta,
        approx_tokens_changed=approx_tokens_changed,
        changed_files=kept_changed_files,
        top_changed_files=top_changed_files,
        omitted_changed_files=omitted_changed_files,
    )


def _iter_workspace_files(project_path: Path) -> Iterator[Path]:
    """Return candidate files for workspace snapshotting."""
    for root, dirs, filenames in os.walk(project_path, topdown=True, followlinks=False):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        root_path = Path(root)
        for filename in filenames:
            if filename in _IGNORED_FILES:
                continue
            file_path = root_path / filename
            if file_path.is_symlink():
                continue
            yield file_path


def _snapshot_file(path: Path) -> FileSnapshot | None:
    """Capture state for a single file."""
    try:
        size = path.stat().st_size
    except OSError:
        return None

    digest = hashlib.sha1()
    sniff = bytearray()
    inline_data: bytearray | None = (
        bytearray() if size <= _MAX_INLINE_TEXT_BYTES else None
    )

    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                if len(sniff) < _TEXT_SNIFF_BYTES:
                    needed = _TEXT_SNIFF_BYTES - len(sniff)
                    sniff.extend(chunk[:needed])
                if inline_data is not None:
                    inline_data.extend(chunk)
    except OSError:
        return None

    is_text = _is_probably_text(bytes(sniff))
    content: str | None = None

    if is_text and inline_data is not None:
        try:
            content = inline_data.decode("utf-8")
        except UnicodeDecodeError:
            is_text = False
            content = None

    return FileSnapshot(
        size_bytes=size,
        digest=digest.hexdigest(),
        is_text=is_text,
        content=content,
    )


def _is_probably_text(sample: bytes) -> bool:
    """Heuristic text/binary detector for provenance metrics."""
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _diff_text(before: str, after: str) -> tuple[int, int]:
    """Approximate textual delta using line-level sequence matching."""
    import difflib

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines)

    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += sum(len(line) for line in before_lines[i1:i2])
        if tag in ("replace", "insert"):
            added += sum(len(line) for line in after_lines[j1:j2])
    return added, removed


def _estimate_text_size(file_state: FileSnapshot) -> int:
    """Estimate text payload size for added/deleted files."""
    if not file_state.is_text:
        return 0
    if file_state.content is not None:
        return len(file_state.content)
    return file_state.size_bytes


def _approximate_tokens(chars: int) -> int:
    """Approximate token count from character volume."""
    if chars <= 0:
        return 0
    return (chars + 3) // 4
