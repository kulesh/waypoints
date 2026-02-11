"""Iteration kickoff prompt construction for fly executor retries."""

from collections.abc import Sequence


def build_iteration_kickoff_prompt(
    *,
    reason_code: str,
    reason_detail: str,
    completion_marker: str,
    waypoint_id: str,
    waypoint_title: str,
    waypoint_objective: str,
    acceptance_criteria: Sequence[str],
    verified_criteria: set[int],
    spec_context_summary: str,
    spec_section_refs: Sequence[str],
    waypoint_spec_hash: str | None,
    current_spec_hash: str | None,
    spec_context_stale: bool,
) -> str:
    """Build a focused kickoff prompt for follow-up iterations."""
    bounded_reason_detail = _truncate_detail(reason_detail, max_chars=900)
    spec_summary = _truncate_detail(spec_context_summary, max_chars=700)
    if not spec_summary:
        spec_summary = "No chart-time waypoint summary is available."

    section_refs = (
        "\n".join(f"- {ref}" for ref in spec_section_refs[:8])
        if spec_section_refs
        else "- No section references recorded."
    )
    unresolved_lines = [
        f"- [ ] [{idx}] {text}"
        for idx, text in enumerate(acceptance_criteria)
        if idx not in verified_criteria
    ]
    unresolved_block = (
        "\n".join(unresolved_lines)
        if unresolved_lines
        else "- All criteria appear verified; finish protocol and validation."
    )
    return (
        "Iteration kickoff\n"
        f"Reason: {reason_code}\n"
        f"Details: {bounded_reason_detail}\n\n"
        f"Current waypoint: {waypoint_id} - {waypoint_title}\n"
        f"Objective: {waypoint_objective}\n\n"
        "Spec context capsule:\n"
        f"- Chart-time summary: {spec_summary}\n"
        "- Relevant product-spec sections:\n"
        f"{section_refs}\n"
        "- Canonical full spec: docs/product-spec.md\n"
        f"- waypoint spec hash: {waypoint_spec_hash or 'unknown'}\n"
        f"- current spec hash: {current_spec_hash or 'unknown'}\n"
        f"- Context stale: {'yes' if spec_context_stale else 'no'}\n\n"
        "Unresolved criteria:\n"
        f"{unresolved_block}\n\n"
        "Required next action:\n"
        "- Continue implementation from current filesystem state.\n"
        "- If summary and full spec conflict, follow docs/product-spec.md.\n"
        "- Run only necessary validations for changed code.\n"
        "- Report structured execution stages.\n\n"
        "Completion rule (strict):\n"
        f"- Emit exactly: {completion_marker}\n"
        "- Do not use aliases like WAYPOINT_COMPLETE.\n"
        "- Do not re-audit unrelated waypoints."
    )


def _truncate_detail(text: str, *, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return f"{stripped[:max_chars]}... (truncated)"
