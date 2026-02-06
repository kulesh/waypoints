"""Product-spec context helpers for waypoint generation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def normalize_section_ref(value: str) -> str:
    """Normalize section references for loose matching."""
    normalized = value.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def extract_spec_section_headings(spec_text: str) -> tuple[str, ...]:
    """Extract markdown heading text from a product spec."""
    headings: list[str] = []
    for match in _HEADING_PATTERN.findall(spec_text):
        heading = match.strip().rstrip("#").strip()
        if heading:
            headings.append(heading)
    # Preserve order while deduplicating
    unique: list[str] = []
    seen: set[str] = set()
    for heading in headings:
        key = normalize_section_ref(heading)
        if key in seen:
            continue
        seen.add(key)
        unique.append(heading)
    return tuple(unique)


def section_ref_exists(ref: str, headings: Iterable[str]) -> bool:
    """Check whether a section reference maps to known headings."""
    normalized_ref = normalize_section_ref(ref)
    if not normalized_ref:
        return False
    normalized_headings = [normalize_section_ref(item) for item in headings]
    if normalized_ref in normalized_headings:
        return True
    # Allow loose contains match to tolerate minor heading drift.
    return any(
        normalized_ref in heading or heading in normalized_ref
        for heading in normalized_headings
    )


def compute_spec_hash(spec_text: str) -> str:
    """Compute stable hash for spec freshness checks."""
    return hashlib.sha256(spec_text.encode("utf-8")).hexdigest()[:20]
