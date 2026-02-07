"""CHART phase delegate — flight plan generation and waypoint CRUD.

Owns the business logic for generating flight plans from product specs,
breaking down waypoints, adding/removing/reordering waypoints, and
managing the waypoint graph.
"""

import logging
from typing import TYPE_CHECKING

from waypoints.llm.client import ChatClient, StreamChunk
from waypoints.llm.prompts import (
    CHART_SYSTEM_PROMPT,
    REPRIORITIZE_PROMPT,
    WAYPOINT_ADD_PROMPT,
    WAYPOINT_BREAKDOWN_PROMPT,
    WAYPOINT_GENERATION_PROMPT,
)
from waypoints.llm.validation import (
    WaypointValidationError,
    validate_reprioritization,
    validate_single_waypoint,
    validate_waypoints,
)
from waypoints.models import FlightPlan, Waypoint, WaypointStatus
from waypoints.orchestration.types import ChunkCallback
from waypoints.spec import compute_spec_hash, extract_spec_section_headings

if TYPE_CHECKING:
    from waypoints.orchestration.coordinator import JourneyCoordinator

logger = logging.getLogger(__name__)


def _build_chart_retry_prompt(prompt: str, errors: list[str]) -> str:
    error_text = "\n".join(f"- {error}" for error in errors)
    return (
        f"{prompt}\n\n"
        "The previous response failed validation with these errors:\n"
        f"{error_text}\n\n"
        "Fix the issues and output ONLY the JSON array. Ensure every waypoint "
        "has a non-empty acceptance_criteria list, a meaningful "
        "spec_context_summary, and non-empty spec_section_refs."
    )


class ChartPhase:
    """Flight plan generation and waypoint CRUD operations."""

    def __init__(self, coordinator: "JourneyCoordinator") -> None:
        self._coord = coordinator

    # ─── Flight Plan Generation ───────────────────────────────────────

    def generate_flight_plan(
        self,
        spec: str,
        on_chunk: ChunkCallback | None = None,
    ) -> FlightPlan:
        """Generate flight plan from product specification.

        Args:
            spec: The product specification text
            on_chunk: Callback for streaming progress

        Returns:
            Generated FlightPlan
        """
        spec_with_notes = self._append_resolution_notes(spec)
        spec_sections = set(extract_spec_section_headings(spec))
        spec_hash = compute_spec_hash(spec)
        prompt = WAYPOINT_GENERATION_PROMPT.format(spec=spec_with_notes)
        logger.info("Generating waypoints from spec: %d chars", len(spec))

        full_response = self._stream_chart_response(prompt, on_chunk)
        try:
            waypoints = self._parse_waypoints(
                full_response,
                spec_sections=spec_sections,
                spec_hash=spec_hash,
            )
        except WaypointValidationError as exc:
            logger.warning("Chart validation failed, retrying: %s", exc.errors)
            retry_prompt = _build_chart_retry_prompt(prompt, exc.errors)
            full_response = self._stream_chart_response(retry_prompt, on_chunk)
            waypoints = self._parse_waypoints(
                full_response,
                spec_sections=spec_sections,
                spec_hash=spec_hash,
            )

        # Create flight plan
        flight_plan = FlightPlan(waypoints=waypoints)
        self._coord._flight_plan = flight_plan

        # Save to disk
        self._coord.save_flight_plan()

        # Log initial generation to audit trail
        self._coord._log_waypoint_event(
            "generated",
            {"waypoints": [wp.to_dict() for wp in waypoints]},
        )

        logger.info("Generated flight plan with %d waypoints", len(waypoints))
        return flight_plan

    def break_down_waypoint(
        self,
        waypoint: Waypoint,
        on_chunk: ChunkCallback | None = None,
    ) -> list[Waypoint]:
        """Break down a waypoint into sub-waypoints.

        Args:
            waypoint: The parent waypoint to break down
            on_chunk: Callback for streaming progress

        Returns:
            List of generated sub-waypoints

        Raises:
            ValueError: If waypoint is already an epic (has children)
        """
        if self._coord.flight_plan and self._coord.flight_plan.is_epic(waypoint.id):
            raise ValueError(f"{waypoint.id} already has sub-waypoints")

        # Format prompt
        criteria_str = "\n".join(f"- {c}" for c in waypoint.acceptance_criteria)
        if not criteria_str:
            criteria_str = "(none specified)"
        resolution_notes = "\n".join(f"- {n}" for n in waypoint.resolution_notes)
        if not resolution_notes:
            resolution_notes = "(none)"
        parent_refs = ", ".join(waypoint.spec_section_refs) or "(none)"
        parent_spec_context = waypoint.spec_context_summary or "(none)"
        spec_text = self._coord.product_spec
        spec_sections = set(extract_spec_section_headings(spec_text))
        spec_hash = compute_spec_hash(spec_text) if spec_text else None
        spec_excerpt = spec_text[:4000] + ("..." if len(spec_text) > 4000 else "")
        if not spec_excerpt:
            spec_excerpt = "(no product spec available)"

        prompt = WAYPOINT_BREAKDOWN_PROMPT.format(
            parent_id=waypoint.id,
            title=waypoint.title,
            objective=waypoint.objective,
            criteria=criteria_str,
            resolution_notes=resolution_notes,
            parent_spec_context_summary=parent_spec_context,
            parent_spec_section_refs=parent_refs,
            spec_excerpt=spec_excerpt,
        )

        logger.info("Breaking down waypoint: %s", waypoint.id)

        full_response = self._stream_chart_response(prompt, on_chunk)

        # Parse sub-waypoints (pass existing IDs for validation)
        existing_ids = (
            {wp.id for wp in self._coord.flight_plan.waypoints}
            if self._coord.flight_plan
            else set()
        )
        try:
            sub_waypoints = self._parse_waypoints(
                full_response,
                existing_ids,
                spec_sections=spec_sections,
                spec_hash=spec_hash,
            )
        except WaypointValidationError as exc:
            logger.warning("Chart validation failed, retrying: %s", exc.errors)
            retry_prompt = _build_chart_retry_prompt(prompt, exc.errors)
            full_response = self._stream_chart_response(retry_prompt, on_chunk)
            sub_waypoints = self._parse_waypoints(
                full_response,
                existing_ids,
                spec_sections=spec_sections,
                spec_hash=spec_hash,
            )

        # Ensure all have correct parent_id
        for wp in sub_waypoints:
            wp.parent_id = waypoint.id

        logger.info(
            "Generated %d sub-waypoints for %s", len(sub_waypoints), waypoint.id
        )
        return sub_waypoints

    def _stream_chart_response(
        self,
        prompt: str,
        on_chunk: ChunkCallback | None = None,
    ) -> str:
        """Stream chart response text from the LLM."""
        if self._coord.llm is None:
            self._coord.llm = ChatClient(
                metrics_collector=self._coord.metrics,
                phase="chart",
            )

        full_response = ""
        for result in self._coord.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=CHART_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                if on_chunk:
                    on_chunk(result.text)
        return full_response

    def generate_waypoint(
        self,
        description: str,
        spec_summary: str | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> tuple[Waypoint, str | None]:
        """Generate a single waypoint from description.

        Args:
            description: User's description of what the waypoint should do
            spec_summary: Optional truncated product spec for context
            on_chunk: Callback for streaming progress

        Returns:
            Tuple of (waypoint, insert_after_id or None)

        Raises:
            WaypointValidationError: If generated waypoint fails validation
        """
        if self._coord.flight_plan is None:
            raise RuntimeError("No flight plan loaded")

        # Create LLM client if needed
        if self._coord.llm is None:
            self._coord.llm = ChatClient(
                metrics_collector=self._coord.metrics,
                phase="chart",
            )

        next_id = self._next_waypoint_id()
        existing_ids = {wp.id for wp in self._coord.flight_plan.waypoints}

        # Format existing waypoints for context
        existing_waypoints = "\n".join(
            f"- {wp.id}: {wp.title}"
            for wp in self._coord.flight_plan.get_root_waypoints()
        )

        # Use provided spec_summary or empty string
        spec_source = spec_summary or self._coord.product_spec
        spec_sections = set(extract_spec_section_headings(spec_source or ""))
        spec_hash = compute_spec_hash(spec_source) if spec_source else None
        spec_context = spec_source or "No product spec available"
        spec_context = self._append_resolution_notes(spec_context)

        prompt = WAYPOINT_ADD_PROMPT.format(
            description=description,
            existing_waypoints=existing_waypoints or "No existing waypoints",
            spec_summary=spec_context,
            next_id=next_id,
        )

        logger.info("Generating waypoint from description: %s", description[:100])

        # Stream response from LLM
        full_response = ""
        for result in self._coord.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=CHART_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Validate the response
        validation = validate_single_waypoint(
            full_response,
            existing_ids,
            require_spec_context=True,
            spec_sections=spec_sections,
        )
        if not validation.valid:
            raise WaypointValidationError(validation.errors)

        # Create waypoint from validated data
        data = validation.data
        assert data is not None
        waypoint = Waypoint(
            id=data["id"],
            title=data["title"],
            objective=data["objective"],
            acceptance_criteria=data.get("acceptance_criteria", []),
            debug_of=data.get("debug_of"),
            resolution_notes=data.get("resolution_notes", []),
            dependencies=data.get("dependencies", []),
            spec_context_summary=data.get("spec_context_summary", ""),
            spec_section_refs=data.get("spec_section_refs", []),
            spec_context_hash=spec_hash,
            status=WaypointStatus.PENDING,
        )

        logger.info("Generated waypoint: %s", waypoint.id)
        return waypoint, validation.insert_after

    def suggest_reprioritization(
        self,
        spec_summary: str | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> tuple[list[str], str, list[dict[str, str]]]:
        """Suggest optimal waypoint order.

        Args:
            spec_summary: Optional truncated product spec for context
            on_chunk: Callback for streaming progress

        Returns:
            Tuple of (new_order, rationale, changes) where:
            - new_order: List of waypoint IDs in suggested order
            - rationale: Explanation for the new order
            - changes: List of per-waypoint change reasons

        Raises:
            RuntimeError: If no flight plan or fewer than 2 waypoints
            WaypointValidationError: If reprioritization response invalid
        """
        import json

        if self._coord.flight_plan is None:
            raise RuntimeError("No flight plan loaded")

        root_waypoints = self._coord.flight_plan.get_root_waypoints()
        if len(root_waypoints) < 2:
            raise RuntimeError("Need at least 2 waypoints to reprioritize")

        # Create LLM client if needed
        if self._coord.llm is None:
            self._coord.llm = ChatClient(
                metrics_collector=self._coord.metrics,
                phase="chart",
            )

        # Format waypoints for context
        waypoints_json = json.dumps(
            [
                {"id": wp.id, "title": wp.title, "dependencies": wp.dependencies}
                for wp in root_waypoints
            ],
            indent=2,
        )

        spec_context = spec_summary or "No product spec available"

        prompt = REPRIORITIZE_PROMPT.format(
            waypoints_json=waypoints_json,
            spec_summary=spec_context,
        )

        logger.info("Generating reprioritization suggestion")

        # Stream response from LLM
        full_response = ""
        for result in self._coord.llm.stream_message(
            messages=[{"role": "user", "content": prompt}],
            system=CHART_SYSTEM_PROMPT,
        ):
            if isinstance(result, StreamChunk):
                full_response += result.text
                if on_chunk:
                    on_chunk(result.text)

        # Validate response
        root_ids = {wp.id for wp in root_waypoints}
        validation = validate_reprioritization(full_response, root_ids)
        if not validation.valid:
            raise WaypointValidationError(validation.errors)

        logger.info("Reprioritization suggested: %s", validation.new_order)
        return validation.new_order, validation.rationale, validation.changes

    # ─── Waypoint Parsing ─────────────────────────────────────────────

    def _parse_waypoints(
        self,
        response: str,
        existing_ids: set[str] | None = None,
        *,
        spec_sections: set[str] | None = None,
        spec_hash: str | None = None,
    ) -> list[Waypoint]:
        """Parse and validate waypoints from LLM response.

        Args:
            response: Raw LLM response containing waypoint JSON
            existing_ids: Set of existing waypoint IDs (for sub-waypoint validation)

        Returns:
            List of validated Waypoint objects

        Raises:
            WaypointValidationError: If validation fails
        """
        result = validate_waypoints(
            response,
            existing_ids,
            require_spec_context=True,
            spec_sections=spec_sections,
        )

        if not result.valid:
            raise WaypointValidationError(result.errors)

        waypoints = []
        for item in result.data or []:
            wp = Waypoint(
                id=item["id"],
                title=item["title"],
                objective=item["objective"],
                acceptance_criteria=item.get("acceptance_criteria", []),
                parent_id=item.get("parent_id"),
                debug_of=item.get("debug_of"),
                resolution_notes=item.get("resolution_notes", []),
                dependencies=item.get("dependencies", []),
                spec_context_summary=item.get("spec_context_summary", ""),
                spec_section_refs=item.get("spec_section_refs", []),
                spec_context_hash=spec_hash,
                status=WaypointStatus.PENDING,
            )
            waypoints.append(wp)

        logger.info("Parsed %d waypoints from LLM response", len(waypoints))
        return waypoints

    def _next_waypoint_id(self) -> str:
        """Generate next available waypoint ID."""
        if self._coord.flight_plan is None:
            return "WP-001"
        existing = {wp.id for wp in self._coord.flight_plan.waypoints}
        for i in range(1, 1000):
            candidate = f"WP-{i:03d}"
            if candidate not in existing:
                return candidate
        return "WP-999"  # Fallback

    def _append_resolution_notes(self, spec: str) -> str:
        """Append resolution notes to the spec for prompt context."""
        if self._coord.flight_plan is None:
            return spec

        notes: list[str] = []
        for wp in self._coord.flight_plan.waypoints:
            if not wp.resolution_notes:
                continue
            note_text = "; ".join(wp.resolution_notes)
            notes.append(f"- {wp.id} {wp.title}: {note_text}")

        if not notes:
            return spec

        notes_block = "\n".join(notes)
        return f"{spec}\n\n## Waypoint Resolution Notes\n{notes_block}"

    # ─── Waypoint CRUD ────────────────────────────────────────────────

    @staticmethod
    def _execution_definition_changed(before: Waypoint, after: Waypoint) -> bool:
        """Return True when edits should trigger re-execution."""
        return (
            before.objective != after.objective
            or before.acceptance_criteria != after.acceptance_criteria
            or before.dependencies != after.dependencies
        )

    def update_waypoint(self, waypoint: Waypoint) -> None:
        """Update a waypoint and persist changes."""
        if self._coord.flight_plan is None:
            return

        # Capture before state for audit log
        existing = self._coord.flight_plan.get_waypoint(waypoint.id)
        before_data = existing.to_dict() if existing else {}

        # If execution-defining fields changed, force waypoint back to pending so
        # it can be rerun in FLY.
        if existing and self._execution_definition_changed(existing, waypoint):
            if waypoint.status != WaypointStatus.PENDING:
                waypoint.status = WaypointStatus.PENDING
                waypoint.completed_at = None
                logger.info(
                    "Reset waypoint %s to PENDING after execution-definition edit",
                    waypoint.id,
                )

        self._coord.flight_plan.update_waypoint(waypoint)
        self._coord.save_flight_plan()

        # Log to audit trail
        self._coord._log_waypoint_event(
            "updated",
            {
                "waypoint_id": waypoint.id,
                "before": before_data,
                "after": waypoint.to_dict(),
            },
        )

    def delete_waypoint(self, waypoint_id: str) -> list[str]:
        """Delete a waypoint and return IDs of orphaned dependents.

        Also removes any children if it's an epic.

        Args:
            waypoint_id: ID of waypoint to delete

        Returns:
            List of waypoint IDs that had this as a dependency
        """
        if self._coord.flight_plan is None:
            return []

        # Capture waypoint data before deletion for audit log
        waypoint = self._coord.flight_plan.get_waypoint(waypoint_id)
        waypoint_data = waypoint.to_dict() if waypoint else {}

        # Get dependents before deletion
        dependents = self._coord.flight_plan.get_dependents(waypoint_id)
        dependent_ids = [wp.id for wp in dependents]

        # Remove the waypoint (FlightPlan handles children)
        self._coord.flight_plan.remove_waypoint(waypoint_id)

        # Save to disk
        self._coord.save_flight_plan()

        # Log to audit trail
        self._coord._log_waypoint_event(
            "deleted",
            {
                "waypoint_id": waypoint_id,
                "waypoint": waypoint_data,
            },
        )

        logger.info(
            "Deleted waypoint %s (orphaned %d dependents)",
            waypoint_id,
            len(dependent_ids),
        )
        return dependent_ids

    def add_sub_waypoints(self, parent_id: str, sub_waypoints: list[Waypoint]) -> None:
        """Add sub-waypoints to a parent waypoint.

        Inserts after parent to maintain tree order.

        Args:
            parent_id: ID of the parent waypoint
            sub_waypoints: List of child waypoints to add
        """
        if self._coord.flight_plan is None:
            return

        # Ensure all have correct parent_id
        for wp in sub_waypoints:
            wp.parent_id = parent_id

        # Insert after parent
        self._coord.flight_plan.insert_waypoints_after(parent_id, sub_waypoints)

        # Save to disk
        self._coord.save_flight_plan()

        # Log to audit trail
        self._coord._log_waypoint_event(
            "broken_down",
            {
                "parent_id": parent_id,
                "sub_waypoints": [wp.to_dict() for wp in sub_waypoints],
            },
        )

        logger.info("Added %d sub-waypoints to %s", len(sub_waypoints), parent_id)

    def add_waypoint(self, waypoint: Waypoint, after_id: str | None = None) -> None:
        """Add a new waypoint to the flight plan.

        Args:
            waypoint: The waypoint to add
            after_id: Insert after this waypoint ID. If None, append to end.
        """
        if self._coord.flight_plan is None:
            return

        if after_id:
            self._coord.flight_plan.insert_waypoint_at(waypoint, after_id)
        else:
            self._coord.flight_plan.add_waypoint(waypoint)

        self._coord.save_flight_plan()

        # Log to audit trail
        self._coord._log_waypoint_event(
            "added",
            {
                "waypoint": waypoint.to_dict(),
                "insert_after": after_id,
            },
        )

        logger.info("Added waypoint %s (after %s)", waypoint.id, after_id or "end")

    def fork_debug_waypoint(self, waypoint: Waypoint, note: str) -> Waypoint:
        """Create a debug waypoint forked from an existing waypoint.

        Args:
            waypoint: The waypoint to debug.
            note: The debug note describing the issue to fix.

        Returns:
            The newly created debug waypoint.
        """
        if self._coord.flight_plan is None:
            raise RuntimeError("No flight plan loaded")

        note_text = note.strip()
        combined_notes = list(waypoint.resolution_notes)
        if note_text:
            combined_notes.append(note_text)

        debug_waypoint = Waypoint(
            id=self._next_waypoint_id(),
            title=f"Debug: {waypoint.title}",
            objective=waypoint.objective,
            acceptance_criteria=list(waypoint.acceptance_criteria),
            debug_of=waypoint.id,
            resolution_notes=combined_notes,
            dependencies=[waypoint.id],
            spec_context_summary=waypoint.spec_context_summary,
            spec_section_refs=list(waypoint.spec_section_refs),
            spec_context_hash=waypoint.spec_context_hash,
            status=WaypointStatus.PENDING,
        )

        if note_text:
            waypoint.resolution_notes.append(note_text)
            self._coord.flight_plan.update_waypoint(waypoint)

        self._coord.flight_plan.insert_waypoint_at(debug_waypoint, waypoint.id)
        self._coord.save_flight_plan()

        self._coord._log_waypoint_event(
            "debug_forked",
            {
                "waypoint_id": waypoint.id,
                "debug_waypoint": debug_waypoint.to_dict(),
                "note": note_text,
            },
        )

        logger.info("Forked debug waypoint %s from %s", debug_waypoint.id, waypoint.id)
        return debug_waypoint

    def reorder_waypoints(
        self,
        new_order: list[str],
        rationale: str = "",
        changes: list[dict[str, str]] | None = None,
    ) -> None:
        """Reorder root waypoints and log the change.

        Args:
            new_order: List of root waypoint IDs in the new order
            rationale: AI's explanation for the new order
            changes: Optional list of per-waypoint change reasons
        """
        if self._coord.flight_plan is None:
            return

        # Capture previous order for audit log
        previous_order = [wp.id for wp in self._coord.flight_plan.get_root_waypoints()]

        # Reorder
        self._coord.flight_plan.reorder_waypoints(new_order)
        self._coord.save_flight_plan()

        # Log to audit trail
        self._coord._log_waypoint_event(
            "reprioritized",
            {
                "previous_order": previous_order,
                "new_order": new_order,
                "rationale": rationale,
                "changes": changes or [],
            },
        )

        prev_summary = " -> ".join(previous_order[:3])
        new_summary = " -> ".join(new_order[:3])
        if len(previous_order) > 3:
            prev_summary += "..."
        if len(new_order) > 3:
            new_summary += "..."
        logger.info("Reordered waypoints: %s -> %s", prev_summary, new_summary)
