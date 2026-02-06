"""Persistent project memory index for tool policy and prompt guidance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

MEMORY_SCHEMA_VERSION = "v1"
STACK_PROFILE_FILENAME = "stack-profile.v1.json"
DIRECTORY_MAP_FILENAME = "directory-map.v1.json"
PROJECT_INDEX_FILENAME = "project-index.v1.json"
POLICY_OVERRIDES_FILENAME = "policy-overrides.v1.json"

DirectoryRole = Literal[
    "source",
    "tests",
    "docs",
    "tooling",
    "config",
    "dependency",
    "build",
    "cache",
    "runtime",
    "metadata",
    "other",
]

# Non-negotiable safety boundaries.
IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS: frozenset[str] = frozenset(
    {".git", ".waypoints", "sessions", "receipts"}
)

_GENERIC_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        "dist",
        "build",
        "out",
        "coverage",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
    }
)

_STACK_IGNORED_DIRS: dict[str, frozenset[str]] = {
    "python": frozenset({".venv", "venv", ".tox", ".nox"}),
    "javascript": frozenset({"node_modules", ".next", ".nuxt"}),
    "typescript": frozenset({"node_modules", ".next", ".nuxt"}),
    "rust": frozenset({"target"}),
    "go": frozenset({"bin"}),
}

_SOURCE_ROOT_HINTS: frozenset[str] = frozenset(
    {"src", "app", "lib", "cmd", "packages", "package"}
)
_TEST_ROOT_HINTS: frozenset[str] = frozenset({"tests", "test", "spec", "__tests__"})
_DOC_ROOT_HINTS: frozenset[str] = frozenset({"docs", "doc"})
_TOOLING_ROOT_HINTS: frozenset[str] = frozenset({"scripts", "tools"})


@dataclass(slots=True, frozen=True)
class ProjectDirectoryRecord:
    """A classified top-level path in the project."""

    name: str
    kind: Literal["dir", "file"]
    role: DirectoryRole
    ignore_for_search: bool
    blocked_for_tools: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize record to JSON-compatible dictionary."""
        return {
            "name": self.name,
            "kind": self.kind,
            "role": self.role,
            "ignore_for_search": self.ignore_for_search,
            "blocked_for_tools": self.blocked_for_tools,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectDirectoryRecord":
        """Deserialize record from JSON dictionary."""
        return cls(
            name=str(data["name"]),
            kind="dir" if data.get("kind") == "dir" else "file",
            role=_safe_role(data.get("role")),
            ignore_for_search=bool(data.get("ignore_for_search", False)),
            blocked_for_tools=bool(data.get("blocked_for_tools", False)),
            reason=str(data.get("reason", "")),
        )


@dataclass(slots=True, frozen=True)
class StackProfile:
    """Persisted stack detection metadata."""

    schema_version: str
    generated_at_utc: str
    stack_types: tuple[str, ...]
    manifest_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize stack profile."""
        return {
            "schema_version": self.schema_version,
            "generated_at_utc": self.generated_at_utc,
            "stack_types": list(self.stack_types),
            "manifest_paths": list(self.manifest_paths),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StackProfile":
        """Deserialize stack profile."""
        raw_types = data.get("stack_types", [])
        raw_manifests = data.get("manifest_paths", [])
        return cls(
            schema_version=str(data.get("schema_version", MEMORY_SCHEMA_VERSION)),
            generated_at_utc=str(data.get("generated_at_utc", "")),
            stack_types=tuple(str(item) for item in raw_types),
            manifest_paths=tuple(str(item) for item in raw_manifests),
        )


@dataclass(slots=True, frozen=True)
class DirectoryMap:
    """Persisted directory classification map."""

    schema_version: str
    generated_at_utc: str
    records: tuple[ProjectDirectoryRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize directory map."""
        return {
            "schema_version": self.schema_version,
            "generated_at_utc": self.generated_at_utc,
            "records": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DirectoryMap":
        """Deserialize directory map."""
        raw_records = data.get("records", [])
        records = tuple(
            ProjectDirectoryRecord.from_dict(record)
            for record in raw_records
            if isinstance(record, dict)
        )
        return cls(
            schema_version=str(data.get("schema_version", MEMORY_SCHEMA_VERSION)),
            generated_at_utc=str(data.get("generated_at_utc", "")),
            records=records,
        )


@dataclass(slots=True, frozen=True)
class ProjectMemoryIndex:
    """Top-level index used by runtime policy checks and prompts."""

    schema_version: str
    generated_at_utc: str
    project_root: str
    top_level_fingerprint: str
    blocked_top_level_dirs: tuple[str, ...]
    ignored_top_level_dirs: tuple[str, ...]
    focus_top_level_dirs: tuple[str, ...]
    source_branch: str | None = None
    policy_overrides_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize project index."""
        return {
            "schema_version": self.schema_version,
            "generated_at_utc": self.generated_at_utc,
            "project_root": self.project_root,
            "top_level_fingerprint": self.top_level_fingerprint,
            "blocked_top_level_dirs": list(self.blocked_top_level_dirs),
            "ignored_top_level_dirs": list(self.ignored_top_level_dirs),
            "focus_top_level_dirs": list(self.focus_top_level_dirs),
            "source_branch": self.source_branch,
            "policy_overrides_digest": self.policy_overrides_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectMemoryIndex":
        """Deserialize project index."""
        return cls(
            schema_version=str(data.get("schema_version", MEMORY_SCHEMA_VERSION)),
            generated_at_utc=str(data.get("generated_at_utc", "")),
            project_root=str(data.get("project_root", "")),
            top_level_fingerprint=str(data.get("top_level_fingerprint", "")),
            blocked_top_level_dirs=tuple(
                str(item) for item in data.get("blocked_top_level_dirs", [])
            ),
            ignored_top_level_dirs=tuple(
                str(item) for item in data.get("ignored_top_level_dirs", [])
            ),
            focus_top_level_dirs=tuple(
                str(item) for item in data.get("focus_top_level_dirs", [])
            ),
            source_branch=(
                str(data.get("source_branch"))
                if data.get("source_branch") is not None
                else None
            ),
            policy_overrides_digest=(
                str(data.get("policy_overrides_digest"))
                if data.get("policy_overrides_digest") is not None
                else None
            ),
        )


@dataclass(slots=True, frozen=True)
class ProjectMemory:
    """Aggregate memory payload written under `.waypoints/memory`."""

    index: ProjectMemoryIndex
    stack_profile: StackProfile
    directory_map: DirectoryMap


@dataclass(slots=True, frozen=True)
class PolicyOverrides:
    """Project-authored top-level directory policy overrides."""

    block_dirs: tuple[str, ...] = ()
    unblock_dirs: tuple[str, ...] = ()
    ignore_dirs: tuple[str, ...] = ()
    unignore_dirs: tuple[str, ...] = ()
    focus_dirs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize overrides to JSON payload."""
        return {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "block_dirs": list(self.block_dirs),
            "unblock_dirs": list(self.unblock_dirs),
            "ignore_dirs": list(self.ignore_dirs),
            "unignore_dirs": list(self.unignore_dirs),
            "focus_dirs": list(self.focus_dirs),
        }


def memory_dir(project_root: Path) -> Path:
    """Return the memory root path for a project."""
    return project_root / ".waypoints" / "memory"


def policy_overrides_path(project_root: Path) -> Path:
    """Return path to project-authored policy override file."""
    return memory_dir(project_root) / POLICY_OVERRIDES_FILENAME


def load_or_build_project_memory(
    project_root: Path, *, force_refresh: bool = False
) -> ProjectMemory:
    """Load memory files or rebuild if missing/stale."""
    root = project_root.resolve()
    current_branch = _detect_current_branch(root)
    current_overrides = load_policy_overrides(root)
    current_overrides_digest = _policy_overrides_digest(current_overrides)

    if not force_refresh:
        loaded = _try_load_memory(root)
        if loaded is not None:
            current_fingerprint = _compute_top_level_fingerprint(root)
            if (
                loaded.index.top_level_fingerprint == current_fingerprint
                and loaded.index.source_branch == current_branch
                and loaded.index.policy_overrides_digest == current_overrides_digest
            ):
                return loaded

    built = _build_memory(
        root,
        current_branch=current_branch,
        overrides=current_overrides,
        overrides_digest=current_overrides_digest,
    )
    _persist_memory(root, built)
    return built


def format_directory_policy_for_prompt(index: ProjectMemoryIndex) -> str:
    """Create a compact prompt fragment from project memory policy."""
    focus = ", ".join(index.focus_top_level_dirs[:8]) or "(project root)"
    ignore = ", ".join(index.ignored_top_level_dirs[:12]) or "(none)"
    blocked = ", ".join(index.blocked_top_level_dirs[:12]) or "(none)"
    return (
        f"- Focus your search in: {focus}\n"
        f"- Ignore generated/runtime areas: {ignore}\n"
        f"- Tool access is blocked for: {blocked}"
    )


def _build_memory(
    project_root: Path,
    *,
    current_branch: str | None,
    overrides: PolicyOverrides,
    overrides_digest: str,
) -> ProjectMemory:
    """Build memory data from the current project filesystem."""
    now = datetime.now(UTC).isoformat()
    stack_profile = _build_stack_profile(project_root, now)
    ignored_roots_from_stack = set(_GENERIC_IGNORED_DIRS)
    for stack_type in stack_profile.stack_types:
        ignored_roots_from_stack.update(
            _STACK_IGNORED_DIRS.get(stack_type, frozenset())
        )

    records: list[ProjectDirectoryRecord] = []
    blocked_roots: set[str] = set(IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS)
    ignored_roots: set[str] = set(IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS)
    focus_roots: set[str] = set()

    for entry in sorted(project_root.iterdir(), key=lambda item: item.name):
        record = _classify_top_level_entry(entry, ignored_roots_from_stack)
        records.append(record)
        if record.blocked_for_tools and record.kind == "dir":
            blocked_roots.add(record.name)
        if record.ignore_for_search and record.kind == "dir":
            ignored_roots.add(record.name)
        if record.kind == "dir" and not record.ignore_for_search:
            focus_roots.add(record.name)

    _apply_policy_overrides(
        overrides=overrides,
        blocked_roots=blocked_roots,
        ignored_roots=ignored_roots,
        focus_roots=focus_roots,
    )

    directory_map = DirectoryMap(
        schema_version=MEMORY_SCHEMA_VERSION,
        generated_at_utc=now,
        records=tuple(records),
    )
    index = ProjectMemoryIndex(
        schema_version=MEMORY_SCHEMA_VERSION,
        generated_at_utc=now,
        project_root=str(project_root),
        top_level_fingerprint=_compute_top_level_fingerprint(project_root),
        blocked_top_level_dirs=tuple(sorted(blocked_roots)),
        ignored_top_level_dirs=tuple(sorted(ignored_roots)),
        focus_top_level_dirs=tuple(sorted(focus_roots)),
        source_branch=current_branch,
        policy_overrides_digest=overrides_digest,
    )
    return ProjectMemory(
        index=index,
        stack_profile=stack_profile,
        directory_map=directory_map,
    )


def _build_stack_profile(project_root: Path, generated_at: str) -> StackProfile:
    """Build stack profile by scanning manifests."""
    stack_types, manifest_paths = _scan_stack_signals(project_root)
    return StackProfile(
        schema_version=MEMORY_SCHEMA_VERSION,
        generated_at_utc=generated_at,
        stack_types=tuple(stack_types),
        manifest_paths=tuple(sorted(manifest_paths)),
    )


def _scan_stack_signals(project_root: Path) -> tuple[list[str], set[str]]:
    """Detect stack types and manifests at root, then depth-1 subdirectories."""
    stack_types: set[str] = set()
    manifest_paths: set[str] = set()
    for directory in _stack_scan_directories(project_root):
        discovered, manifests = _scan_stack_at_directory(project_root, directory)
        stack_types.update(discovered)
        manifest_paths.update(manifests)
    return sorted(stack_types), manifest_paths


def _stack_scan_directories(project_root: Path) -> list[Path]:
    """Directories to scan for stack markers (root + visible depth-1 children)."""
    directories = [project_root]
    for child in sorted(project_root.iterdir(), key=lambda item: item.name):
        if child.is_dir() and not child.name.startswith("."):
            directories.append(child)
    return directories


def _scan_stack_at_directory(
    project_root: Path, directory: Path
) -> tuple[set[str], set[str]]:
    """Detect stack markers in a single directory."""
    discovered: set[str] = set()
    manifests: set[str] = set()

    def add_manifest(filename: str) -> None:
        manifests.add(str((directory / filename).resolve().relative_to(project_root)))

    python_markers = ("pyproject.toml", "setup.py", "requirements.txt")
    found_python = [name for name in python_markers if (directory / name).exists()]
    if found_python:
        discovered.add("python")
        for marker in found_python:
            add_manifest(marker)

    package_json = directory / "package.json"
    tsconfig_json = directory / "tsconfig.json"
    if package_json.exists():
        discovered.add("typescript" if tsconfig_json.exists() else "javascript")
        add_manifest("package.json")
        if tsconfig_json.exists():
            add_manifest("tsconfig.json")

    if (directory / "go.mod").exists():
        discovered.add("go")
        add_manifest("go.mod")

    if (directory / "Cargo.toml").exists():
        discovered.add("rust")
        add_manifest("Cargo.toml")

    return discovered, manifests


def load_policy_overrides(project_root: Path) -> PolicyOverrides:
    """Load project-authored policy override file, if present."""
    path = policy_overrides_path(project_root)
    if not path.exists():
        return PolicyOverrides()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PolicyOverrides()
    if not isinstance(data, dict):
        return PolicyOverrides()
    return PolicyOverrides(
        block_dirs=_normalize_override_entries(data.get("block_dirs", [])),
        unblock_dirs=_normalize_override_entries(data.get("unblock_dirs", [])),
        ignore_dirs=_normalize_override_entries(data.get("ignore_dirs", [])),
        unignore_dirs=_normalize_override_entries(data.get("unignore_dirs", [])),
        focus_dirs=_normalize_override_entries(data.get("focus_dirs", [])),
    )


def write_default_policy_overrides(project_root: Path) -> Path:
    """Write default policy override template if missing."""
    path = policy_overrides_path(project_root)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(PolicyOverrides().to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _normalize_override_entries(value: Any) -> tuple[str, ...]:
    """Normalize top-level override entries from dynamic JSON payloads."""
    if not isinstance(value, list):
        return ()
    normalized: list[str] = []
    for item in value:
        item_text = str(item).strip()
        if not item_text:
            continue
        candidate = Path(item_text)
        if len(candidate.parts) != 1:
            continue
        normalized.append(candidate.parts[0])
    return tuple(sorted(set(normalized)))


def _apply_policy_overrides(
    *,
    overrides: PolicyOverrides,
    blocked_roots: set[str],
    ignored_roots: set[str],
    focus_roots: set[str],
) -> None:
    """Apply project-authored policy overrides to computed sets."""
    for name in overrides.block_dirs:
        blocked_roots.add(name)
        ignored_roots.add(name)
        focus_roots.discard(name)

    for name in overrides.ignore_dirs:
        ignored_roots.add(name)
        focus_roots.discard(name)

    for name in overrides.unblock_dirs:
        if name in IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS:
            continue
        blocked_roots.discard(name)

    for name in overrides.unignore_dirs:
        ignored_roots.discard(name)

    for name in overrides.focus_dirs:
        if name in blocked_roots:
            continue
        focus_roots.add(name)

    # Maintain invariant: blocked roots are always ignored and never focused.
    for blocked in blocked_roots:
        ignored_roots.add(blocked)
        focus_roots.discard(blocked)


def _policy_overrides_digest(overrides: PolicyOverrides) -> str:
    """Hash override content for stale-memory detection."""
    raw = json.dumps(overrides.to_dict(), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _detect_current_branch(project_root: Path) -> str | None:
    """Detect current git branch without invoking subprocesses."""
    git_dir = _resolve_git_dir(project_root)
    if git_dir is None:
        return None
    head_path = git_dir / "HEAD"
    try:
        head = head_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref: "):
        ref = head.removeprefix("ref: ").strip()
        if ref.startswith("refs/heads/"):
            return ref.removeprefix("refs/heads/")
        return ref
    if head:
        return f"detached:{head[:12]}"
    return None


def _resolve_git_dir(project_root: Path) -> Path | None:
    """Resolve .git directory for normal repos and worktrees."""
    dot_git = project_root / ".git"
    if dot_git.is_dir():
        return dot_git
    if not dot_git.exists() or not dot_git.is_file():
        return None
    try:
        content = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content.startswith("gitdir:"):
        return None
    raw_path = content.split(":", 1)[1].strip()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (project_root / candidate).resolve()
    return candidate if candidate.exists() else None


def _classify_top_level_entry(
    entry: Path, ignored_roots_from_stack: set[str]
) -> ProjectDirectoryRecord:
    """Classify a top-level project entry for prompt/tool policy."""
    kind: Literal["dir", "file"] = "dir" if entry.is_dir() else "file"
    name = entry.name

    if kind == "dir" and name in IMMUTABLE_BLOCKED_TOP_LEVEL_DIRS:
        role: DirectoryRole = (
            "metadata" if name in {".git", ".waypoints"} else "runtime"
        )
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role=role,
            ignore_for_search=True,
            blocked_for_tools=True,
            reason="immutable safety boundary",
        )

    if kind == "dir" and name in ignored_roots_from_stack:
        role = _role_for_ignored_directory(name)
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role=role,
            ignore_for_search=True,
            blocked_for_tools=True,
            reason="generated/cache/dependency directory",
        )

    if kind == "dir" and name in _SOURCE_ROOT_HINTS:
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role="source",
            ignore_for_search=False,
            blocked_for_tools=False,
            reason="source root hint",
        )

    if kind == "dir" and name in _TEST_ROOT_HINTS:
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role="tests",
            ignore_for_search=False,
            blocked_for_tools=False,
            reason="test root hint",
        )

    if kind == "dir" and name in _DOC_ROOT_HINTS:
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role="docs",
            ignore_for_search=False,
            blocked_for_tools=False,
            reason="documentation root hint",
        )

    if kind == "dir" and name in _TOOLING_ROOT_HINTS:
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role="tooling",
            ignore_for_search=False,
            blocked_for_tools=False,
            reason="tooling root hint",
        )

    if kind == "dir" and name.startswith("."):
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role="config",
            ignore_for_search=True,
            blocked_for_tools=False,
            reason="hidden configuration directory",
        )

    if kind == "dir":
        return ProjectDirectoryRecord(
            name=name,
            kind=kind,
            role="other",
            ignore_for_search=False,
            blocked_for_tools=False,
            reason="unclassified directory",
        )

    return ProjectDirectoryRecord(
        name=name,
        kind=kind,
        role="other",
        ignore_for_search=False,
        blocked_for_tools=False,
        reason="top-level file",
    )


def _role_for_ignored_directory(name: str) -> DirectoryRole:
    """Map ignored directory name to a semantic role."""
    dependency_names = {"node_modules", ".venv", "venv"}
    build_names = {"dist", "build", "out", ".next", ".nuxt", "target"}
    cache_names = {
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        ".tox",
        ".nox",
    }

    if name in dependency_names:
        return "dependency"
    if name in build_names:
        return "build"
    if name in cache_names:
        return "cache"
    return "other"


def _compute_top_level_fingerprint(project_root: Path) -> str:
    """Create a stable fingerprint from top-level entry names and kinds."""
    lines: list[str] = []
    for entry in sorted(project_root.iterdir(), key=lambda item: item.name):
        kind = "dir" if entry.is_dir() else "file"
        lines.append(f"{kind}:{entry.name}")
    joined = "\n".join(lines)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:20]


def _try_load_memory(project_root: Path) -> ProjectMemory | None:
    """Try loading existing memory files; return None if unavailable/invalid."""
    root = memory_dir(project_root)
    index_path = root / PROJECT_INDEX_FILENAME
    stack_path = root / STACK_PROFILE_FILENAME
    map_path = root / DIRECTORY_MAP_FILENAME

    if not index_path.exists() or not stack_path.exists() or not map_path.exists():
        return None

    try:
        index = ProjectMemoryIndex.from_dict(_read_json(index_path))
        stack_profile = StackProfile.from_dict(_read_json(stack_path))
        directory_map = DirectoryMap.from_dict(_read_json(map_path))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None

    if (
        index.project_root
        and Path(index.project_root).resolve() != project_root.resolve()
    ):
        return None

    return ProjectMemory(
        index=index,
        stack_profile=stack_profile,
        directory_map=directory_map,
    )


def _persist_memory(project_root: Path, memory: ProjectMemory) -> None:
    """Persist memory payload under `.waypoints/memory`."""
    root = memory_dir(project_root)
    root.mkdir(parents=True, exist_ok=True)

    _write_json(root / STACK_PROFILE_FILENAME, memory.stack_profile.to_dict())
    _write_json(root / DIRECTORY_MAP_FILENAME, memory.directory_map.to_dict())
    _write_json(root / PROJECT_INDEX_FILENAME, memory.index.to_dict())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write deterministic JSON payload."""
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    """Read JSON object from disk."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("Expected JSON object")
    return data


def _safe_role(value: Any) -> DirectoryRole:
    """Convert dynamic role value into a known role literal."""
    known: set[str] = {
        "source",
        "tests",
        "docs",
        "tooling",
        "config",
        "dependency",
        "build",
        "cache",
        "runtime",
        "metadata",
        "other",
    }
    as_str = str(value) if value is not None else "other"
    normalized = as_str if as_str in known else "other"
    return cast(DirectoryRole, normalized)
