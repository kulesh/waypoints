"""CHART phase prompts for waypoint generation and management."""
from __future__ import annotations

# System prompt for all CHART operations
CHART_SYSTEM_PROMPT = (
    "You are a technical project planner. Create clear, testable "
    "waypoints for software development. Output valid JSON only."
)

WAYPOINT_GENERATION_PROMPT = """\
Based on the Product Specification below, generate a flight plan of waypoints
for building this product incrementally.

Each waypoint should:
1. Be independently testable
2. Have clear acceptance criteria
3. Be appropriately sized (1-3 hours of focused work for single-hop)
4. Use parent_id for multi-hop waypoints (epics that contain sub-tasks)

Output as a JSON array of waypoints. Each waypoint has:
- id: String like "WP-001" (use "WP-001a", "WP-001b" for children)
- title: Brief descriptive title
- objective: What this waypoint accomplishes
- acceptance_criteria: Array of testable criteria
- parent_id: ID of parent waypoint (null for top-level)
- dependencies: Array of waypoint IDs this depends on

Generate 8-15 waypoints for MVP scope. Group related work into epics where appropriate.

Output ONLY the JSON array, no markdown code blocks or other text.

Product Specification:
{spec}

Generate the waypoints JSON now:"""

WAYPOINT_BREAKDOWN_PROMPT = """\
Break down the following waypoint into 2-5 smaller sub-waypoints.

Parent Waypoint:
- ID: {parent_id}
- Title: {title}
- Objective: {objective}
- Acceptance Criteria: {criteria}

Each sub-waypoint should:
1. Be independently testable
2. Have clear acceptance criteria
3. Be appropriately sized (1-3 hours of focused work)
4. Together fully cover the parent waypoint's objective

Output as a JSON array. Each sub-waypoint has:
- id: String like "{parent_id}a", "{parent_id}b", etc.
- title: Brief descriptive title
- objective: What this sub-waypoint accomplishes
- acceptance_criteria: Array of testable criteria
- parent_id: "{parent_id}" (the parent waypoint ID)
- dependencies: Array of sibling waypoint IDs this depends on (or empty)

Output ONLY the JSON array, no markdown code blocks or other text.

Generate the sub-waypoints JSON now:"""

WAYPOINT_ADD_PROMPT = """\
Generate a single waypoint based on the user's description.

User's description:
{description}

Existing flight plan waypoints:
{existing_waypoints}

Product Spec (summary):
{spec_summary}

Generate a waypoint that fits logically into the existing plan. Consider:
1. What existing waypoints this should depend on (prerequisites)
2. Where it fits in the execution order
3. Clear, testable acceptance criteria

Output a JSON object with:
- id: "{next_id}"
- title: Brief descriptive title (5-10 words)
- objective: What this waypoint accomplishes (1-2 sentences)
- acceptance_criteria: Array of 2-5 testable success criteria
- dependencies: Array of waypoint IDs this depends on (empty array if none)
- insert_after: ID of waypoint to insert after, or null to append at end

Output ONLY the JSON object, no markdown code blocks or other text.

Generate the waypoint JSON now:"""

REPRIORITIZE_PROMPT = """\
Analyze the following waypoints and suggest an optimal execution order.

Current waypoints (in current order):
{waypoints_json}

Product Specification:
{spec_summary}

Consider:
1. Dependencies - waypoints that depend on others must come after
2. Logical flow - foundational work before features that use it
3. Risk reduction - validate core assumptions early
4. Incremental value - deliver testable increments

Output a JSON object with:
- rationale: Brief explanation of the recommended order (1-2 sentences)
- order: Array of waypoint IDs in recommended order (root-level only)
- changes: Array of objects with "id" and "reason" for each moved waypoint

If current order is already optimal, return the same order with rationale explaining.

Output ONLY the JSON object, no markdown code blocks.

Generate the reprioritization JSON now:"""
