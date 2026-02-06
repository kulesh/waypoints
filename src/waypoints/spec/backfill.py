"""Backfill utilities for waypoint-level product-spec context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint
from waypoints.spec.context import compute_spec_hash

_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_/-]{1,}")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
    "will",
    "this",
    "these",
    "those",
    "into",
    "within",
    "across",
    "about",
    "using",
    "use",
}


@dataclass(frozen=True)
class WaypointSpecContext:
    """Deterministically generated spec context for one waypoint."""

    summary: str
    section_refs: tuple[str, ...]
    spec_hash: str


@dataclass(frozen=True)
class SpecContextRefreshStats:
    """Summary for a refresh operation over a flight plan."""

    total_waypoints: int
    stale_or_missing_waypoints: int
    regenerated_waypoints: int
    unchanged_waypoints: int
    spec_hash: str


@dataclass(frozen=True)
class _SpecSection:
    """Parsed markdown section for relevance scoring."""

    heading: str
    body: str
    order: int


def load_project_spec_text(project_root: Path) -> str:
    """Load canonical product spec text for a project.

    Priority:
    1. docs/product-spec.md
    2. newest docs/product-spec*.md
    """
    docs_path = project_root / "docs"
    canonical = docs_path / "product-spec.md"
    if canonical.exists():
        return canonical.read_text(encoding="utf-8")

    candidates = sorted(
        docs_path.glob("product-spec*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return ""
    return candidates[0].read_text(encoding="utf-8")


def refresh_flight_plan_spec_context(
    flight_plan: FlightPlan,
    spec_text: str,
    *,
    only_stale: bool = False,
) -> SpecContextRefreshStats:
    """Regenerate waypoint spec context fields on an existing flight plan."""
    spec_hash = compute_spec_hash(spec_text)
    stale_or_missing = 0
    regenerated = 0
    unchanged = 0

    for waypoint in flight_plan.waypoints:
        has_context = bool(
            waypoint.spec_context_summary.strip()
            and waypoint.spec_section_refs
            and waypoint.spec_context_hash
        )
        needs_refresh = (not has_context) or (waypoint.spec_context_hash != spec_hash)
        if needs_refresh:
            stale_or_missing += 1
        if only_stale and not needs_refresh:
            unchanged += 1
            continue

        generated = synthesize_waypoint_spec_context(waypoint, spec_text)
        before = (
            waypoint.spec_context_summary,
            tuple(waypoint.spec_section_refs),
            waypoint.spec_context_hash,
        )
        after = (generated.summary, generated.section_refs, generated.spec_hash)
        if before == after:
            unchanged += 1
            continue

        waypoint.spec_context_summary = generated.summary
        waypoint.spec_section_refs = list(generated.section_refs)
        waypoint.spec_context_hash = generated.spec_hash
        regenerated += 1

    return SpecContextRefreshStats(
        total_waypoints=len(flight_plan.waypoints),
        stale_or_missing_waypoints=stale_or_missing,
        regenerated_waypoints=regenerated,
        unchanged_waypoints=unchanged,
        spec_hash=spec_hash,
    )


def synthesize_waypoint_spec_context(
    waypoint: Waypoint,
    spec_text: str,
    *,
    max_section_refs: int = 4,
) -> WaypointSpecContext:
    """Generate waypoint-scoped spec summary and section refs from markdown."""
    sections = _extract_sections(spec_text)
    query_terms = _extract_query_terms(waypoint)
    ranked = sorted(
        sections,
        key=lambda section: (-_score_section(section, query_terms), section.order),
    )
    positive_ranked = [
        section for section in ranked if _score_section(section, query_terms) > 0
    ]
    if positive_ranked:
        selected = positive_ranked[:max_section_refs]
    else:
        selected = ranked[: max(1, min(max_section_refs, 2))]

    refs = tuple(section.heading for section in selected)
    summary = _compose_summary(waypoint, selected, refs)
    return WaypointSpecContext(
        summary=summary,
        section_refs=refs,
        spec_hash=compute_spec_hash(spec_text),
    )


def _extract_sections(spec_text: str) -> list[_SpecSection]:
    lines = spec_text.splitlines()
    sections: list[_SpecSection] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    order = 0

    for raw_line in lines:
        match = _HEADING_PATTERN.match(raw_line)
        if match:
            if current_heading is not None:
                body = "\n".join(current_lines).strip()
                sections.append(
                    _SpecSection(
                        heading=current_heading,
                        body=body,
                        order=order,
                    )
                )
                order += 1
            current_heading = match.group(1).strip().rstrip("#").strip()
            current_lines = []
            continue
        if current_heading is not None:
            current_lines.append(raw_line)

    if current_heading is not None:
        body = "\n".join(current_lines).strip()
        sections.append(
            _SpecSection(
                heading=current_heading,
                body=body,
                order=order,
            )
        )

    if sections:
        return sections
    fallback = spec_text.strip() or "Product specification content."
    return [_SpecSection(heading="Product Specification", body=fallback, order=0)]


def _extract_query_terms(waypoint: Waypoint) -> set[str]:
    source_parts = [
        waypoint.title,
        waypoint.objective,
        *waypoint.acceptance_criteria,
        *waypoint.resolution_notes,
    ]
    return _keywords(" ".join(source_parts))


def _keywords(text: str) -> set[str]:
    tokens = set()
    for match in _TOKEN_PATTERN.findall(text.lower()):
        token = match.strip("-_/")
        if not token or token in _STOP_WORDS:
            continue
        tokens.add(token)
    return tokens


def _score_section(section: _SpecSection, query_terms: set[str]) -> int:
    if not query_terms:
        return 1
    heading_terms = _keywords(section.heading)
    body_terms = _keywords(section.body[:3000])
    heading_overlap = len(query_terms.intersection(heading_terms))
    body_overlap = len(query_terms.intersection(body_terms))
    return (heading_overlap * 4) + body_overlap


def _compose_summary(
    waypoint: Waypoint,
    selected_sections: list[_SpecSection],
    refs: tuple[str, ...],
) -> str:
    objective = waypoint.objective.strip().rstrip(".")
    criteria_focus = [
        item.strip().rstrip(".")
        for item in waypoint.acceptance_criteria
        if item.strip()
    ][:2]
    snippets = [
        _first_requirement_snippet(section.body) for section in selected_sections
    ]
    snippets = [snippet for snippet in snippets if snippet]

    parts: list[str] = []
    if objective:
        parts.append(f"This waypoint implements: {objective}.")
    if criteria_focus:
        parts.append(f"Acceptance focus: {'; '.join(criteria_focus)}.")
    if refs:
        parts.append(f"Relevant product spec sections: {', '.join(refs)}.")
    if snippets:
        parts.append(f"Key requirements: {' '.join(snippets[:2])}")

    summary = " ".join(parts)
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) < 40:
        summary = (
            f"Implement {waypoint.title} using the referenced product spec sections "
            "and verify all acceptance criteria."
        )
    if len(summary) > 1200:
        summary = summary[:1197].rstrip() + "..."
    return summary


def _first_requirement_snippet(body: str) -> str:
    for raw_line in body.splitlines():
        line = raw_line.strip().lstrip("-*").strip()
        if len(line) < 12:
            continue
        if line.startswith("#"):
            continue
        if len(line) > 180:
            return line[:177].rstrip() + "..."
        return line
    body_compact = re.sub(r"\s+", " ", body).strip()
    if not body_compact:
        return ""
    if len(body_compact) > 180:
        return body_compact[:177].rstrip() + "..."
    return body_compact
