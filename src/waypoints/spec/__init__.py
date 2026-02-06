"""Utilities for product-spec context extraction and validation."""

from waypoints.spec.backfill import (
    SpecContextRefreshStats,
    WaypointSpecContext,
    load_project_spec_text,
    refresh_flight_plan_spec_context,
    synthesize_waypoint_spec_context,
)
from waypoints.spec.context import (
    compute_spec_hash,
    extract_spec_section_headings,
    normalize_section_ref,
    section_ref_exists,
)

__all__ = [
    "load_project_spec_text",
    "refresh_flight_plan_spec_context",
    "SpecContextRefreshStats",
    "synthesize_waypoint_spec_context",
    "WaypointSpecContext",
    "compute_spec_hash",
    "extract_spec_section_headings",
    "normalize_section_ref",
    "section_ref_exists",
]
