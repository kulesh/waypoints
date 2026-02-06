"""Tests for deterministic spec-context backfill utilities."""

from waypoints.models.flight_plan import FlightPlan
from waypoints.models.waypoint import Waypoint
from waypoints.spec import (
    compute_spec_hash,
    refresh_flight_plan_spec_context,
    synthesize_waypoint_spec_context,
)


def test_synthesize_waypoint_spec_context_selects_relevant_sections() -> None:
    """Generated context should prioritize sections matching waypoint intent."""
    spec = """# Product Spec

## Search Experience
Users can open search and see fuzzy matches update in real time.
Results must highlight matched text and support quick navigation.

## Keyboard Navigation
Use Enter to open selected item, Escape to clear search, and n/N to cycle.

## Telemetry
Emit analytics events for search opened and result selected actions.
"""
    waypoint = Waypoint(
        id="WP-012",
        title="Search Within Hierarchy",
        objective="Implement fuzzy search and highlighted match results",
        acceptance_criteria=[
            "Fuzzy matching filters hierarchy nodes in real-time",
            "Search matches highlight in results",
        ],
    )

    context = synthesize_waypoint_spec_context(waypoint, spec)

    assert context.summary
    assert len(context.summary) >= 40
    assert context.spec_hash == compute_spec_hash(spec)
    assert "Search Experience" in context.section_refs
    assert "Relevant product spec sections" in context.summary


def test_refresh_flight_plan_spec_context_only_stale_updates_needed() -> None:
    """Only-stale mode should leave current waypoints untouched."""
    spec = """# Product Spec

## Scope
Implement query parsing and execution.
"""
    current_hash = compute_spec_hash(spec)
    stale_waypoint = Waypoint(
        id="WP-001",
        title="Parser",
        objective="Implement parser",
        acceptance_criteria=["Parses query expressions"],
    )
    current_waypoint = Waypoint(
        id="WP-002",
        title="Executor",
        objective="Implement executor",
        acceptance_criteria=["Executes parsed query"],
        spec_context_summary="Current summary.",
        spec_section_refs=["Scope"],
        spec_context_hash=current_hash,
    )
    plan = FlightPlan(waypoints=[stale_waypoint, current_waypoint])

    stats = refresh_flight_plan_spec_context(plan, spec, only_stale=True)

    assert stats.total_waypoints == 2
    assert stats.stale_or_missing_waypoints == 1
    assert stats.regenerated_waypoints == 1
    assert stats.unchanged_waypoints == 1
    assert stale_waypoint.spec_context_hash == current_hash
    assert stale_waypoint.spec_section_refs
    assert current_waypoint.spec_context_summary == "Current summary."
