#!/usr/bin/env python3
"""Test waypoint generation prompt with a product spec file."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from waypoints.llm.client import ChatClient

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


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_waypoint_gen.py <spec-file.md>")
        print("Example: python scripts/test_waypoint_gen.py docs/product-spec.md")
        sys.exit(1)

    spec_path = Path(sys.argv[1])
    if not spec_path.exists():
        print(f"File not found: {spec_path}")
        sys.exit(1)

    spec = spec_path.read_text()
    prompt = WAYPOINT_GENERATION_PROMPT.format(spec=spec)

    print(f"Loaded spec: {len(spec)} chars")
    print("Generating waypoints...\n")

    client = ChatClient()
    response = ""

    system_msg = (
        "You are a technical project planner. "
        "Create clear, testable waypoints for software development. "
        "Output valid JSON only."
    )
    for chunk in client.stream_message(
        messages=[{"role": "user", "content": prompt}],
        system=system_msg,
    ):
        print(chunk, end="", flush=True)
        response += chunk

    print("\n")

    # Try to parse and pretty-print
    try:
        json_match = re.search(r"\[[\s\S]*\]", response)
        if json_match:
            waypoints = json.loads(json_match.group())
            print(f"\n--- Parsed {len(waypoints)} waypoints ---\n")
            for wp in waypoints:
                parent_id = wp.get("parent_id")
                parent = f" (child of {parent_id})" if parent_id else ""
                dep_list = wp.get("dependencies", [])
                deps = f" [deps: {', '.join(dep_list)}]" if dep_list else ""
                print(f"{wp['id']}: {wp['title']}{parent}{deps}")
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")


if __name__ == "__main__":
    main()
