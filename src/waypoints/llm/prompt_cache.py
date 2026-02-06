"""Prompt caching helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_CACHE_RETENTION = "24h"
PROMPT_CACHE_KEY_VERSION = "v1"


def build_prompt_cache_key(
    *,
    provider: str,
    model: str,
    phase: str,
    cwd: str | None,
    mode: str,
) -> str:
    """Build a stable prompt cache key for provider requests.

    The key is stable for a provider/model/phase/project tuple so repeated
    turns within a waypoint and repeated waypoints in the same project can
    reuse cached prompt prefixes.
    """
    project_id = _project_fingerprint(cwd)
    raw = (
        f"waypoints:{PROMPT_CACHE_KEY_VERSION}:{provider}:{model}:{phase}:{mode}:"
        f"{project_id}"
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"waypoints:{phase}:{mode}:{digest}"


def _project_fingerprint(cwd: str | None) -> str:
    """Return a project fingerprint derived from the working directory."""
    if not cwd:
        return "global"

    resolved = str(Path(cwd).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
