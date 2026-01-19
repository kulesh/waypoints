"""Semantic comparison tool using LLM as judge.

Compares two artifacts (product specs, flight plans) for semantic equivalence.
Uses structured prompts to get consistent, parseable verdicts.
"""

import json
import logging
import re
from typing import Any

from waypoints.llm.client import ChatClient, StreamChunk
from waypoints.verify.models import ComparisonResult, ComparisonVerdict

logger = logging.getLogger(__name__)


SPEC_COMPARISON_SYSTEM = """You are a product specification reviewer. Your job is to \
compare two product specifications and determine if they describe the same product.

Focus on semantic equivalence, not exact wording. Two specs are equivalent if:
- They describe the same core features and functionality
- They have the same user stories and use cases
- They specify the same technical requirements
- A developer reading either would build the same product

Minor differences in wording, formatting, or organization don't matter.
What matters is: "Would someone reading both specs understand they're building \
the same thing?"
"""

SPEC_COMPARISON_PROMPT = """Compare these two product specifications:

## Specification A
{spec_a}

## Specification B
{spec_b}

Analyze both specifications and determine if they are semantically equivalent.

Respond with a JSON object in this exact format:
```json
{{
  "verdict": "equivalent" | "different" | "uncertain",
  "confidence": <float 0.0-1.0>,
  "rationale": "<1-2 sentence explanation>",
  "differences": ["<difference 1>", "<difference 2>", ...]
}}
```

- verdict: "equivalent" if specs describe same product, "different" if not, \
"uncertain" if unclear
- confidence: your confidence in the verdict (0.0 = guessing, 1.0 = certain)
- rationale: brief explanation of your reasoning
- differences: list specific differences found (empty if equivalent)

Respond ONLY with the JSON object, no other text."""


PLAN_COMPARISON_SYSTEM = """You are a project planning reviewer. Your job is to \
compare two flight plans (project roadmaps) and determine if they describe the \
same implementation approach.

Focus on semantic equivalence of the waypoints (tasks). Two plans are equivalent if:
- They have waypoints covering the same objectives (even if worded differently)
- The acceptance criteria are functionally equivalent
- The overall implementation approach is the same
- A developer following either plan would build the same thing

Minor differences in task ordering, granularity, or wording don't matter.
What matters is: "Would someone following both plans end up with the same product?"
"""

PLAN_COMPARISON_PROMPT = """Compare these two flight plans:

## Flight Plan A
{plan_a}

## Flight Plan B
{plan_b}

Analyze both flight plans and determine if they are semantically equivalent.

Respond with a JSON object in this exact format:
```json
{{
  "verdict": "equivalent" | "different" | "uncertain",
  "confidence": <float 0.0-1.0>,
  "rationale": "<1-2 sentence explanation>",
  "differences": ["<difference 1>", "<difference 2>", ...]
}}
```

- verdict: "equivalent" if plans describe same implementation, "different" if not, \
"uncertain" if unclear
- confidence: your confidence in the verdict (0.0 = guessing, 1.0 = certain)
- rationale: brief explanation of your reasoning
- differences: list specific differences found (empty if equivalent)

Respond ONLY with the JSON object, no other text."""


def _parse_comparison_response(response: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling markdown code blocks."""
    # Try to extract JSON from code block
    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try raw response
        json_str = response.strip()

    try:
        result: dict[str, Any] = json.loads(json_str)
        return result
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse comparison response: %s", e)
        # Return uncertain verdict on parse failure
        return {
            "verdict": "uncertain",
            "confidence": 0.0,
            "rationale": f"Failed to parse LLM response: {e}",
            "differences": [],
        }


def _format_flight_plan(plan_data: dict[str, Any]) -> str:
    """Format flight plan dict as readable text for comparison."""
    lines = []
    waypoints = plan_data.get("waypoints", [])

    for wp in waypoints:
        lines.append(f"## {wp.get('id', 'unknown')}: {wp.get('title', 'Untitled')}")
        lines.append(f"**Objective:** {wp.get('objective', 'N/A')}")

        criteria = wp.get("acceptance_criteria", [])
        if criteria:
            lines.append("**Acceptance Criteria:**")
            for c in criteria:
                lines.append(f"  - {c}")

        parent = wp.get("parent_id")
        if parent:
            lines.append(f"**Parent:** {parent}")

        lines.append("")

    return "\n".join(lines)


def compare_specs(
    spec_a: str,
    spec_b: str,
    verbose: bool = False,
) -> ComparisonResult:
    """Compare two product specifications for semantic equivalence.

    Args:
        spec_a: First product specification (markdown)
        spec_b: Second product specification (markdown)
        verbose: If True, log streaming output

    Returns:
        ComparisonResult with verdict, confidence, rationale, and differences
    """
    logger.info("Comparing product specs (%d vs %d chars)", len(spec_a), len(spec_b))

    client = ChatClient(phase="verify-compare")
    prompt = SPEC_COMPARISON_PROMPT.format(spec_a=spec_a, spec_b=spec_b)

    response_text = ""
    for result in client.stream_message(
        messages=[{"role": "user", "content": prompt}],
        system=SPEC_COMPARISON_SYSTEM,
        max_tokens=1024,
    ):
        if isinstance(result, StreamChunk):
            response_text += result.text
            if verbose:
                print(result.text, end="", flush=True)

    if verbose:
        print()

    data = _parse_comparison_response(response_text)

    return ComparisonResult(
        verdict=ComparisonVerdict(data.get("verdict", "uncertain")),
        confidence=float(data.get("confidence", 0.0)),
        rationale=data.get("rationale", ""),
        differences=data.get("differences", []),
        artifact_type="spec",
    )


def compare_flight_plans(
    plan_a: dict[str, Any] | str,
    plan_b: dict[str, Any] | str,
    verbose: bool = False,
) -> ComparisonResult:
    """Compare two flight plans for semantic equivalence.

    Args:
        plan_a: First flight plan (dict or JSON string)
        plan_b: Second flight plan (dict or JSON string)
        verbose: If True, log streaming output

    Returns:
        ComparisonResult with verdict, confidence, rationale, and differences
    """
    # Handle string input - parse to dict
    plan_a_dict: dict[str, Any] = (
        json.loads(plan_a) if isinstance(plan_a, str) else plan_a
    )
    plan_b_dict: dict[str, Any] = (
        json.loads(plan_b) if isinstance(plan_b, str) else plan_b
    )

    # Format for comparison
    plan_a_text = _format_flight_plan(plan_a_dict)
    plan_b_text = _format_flight_plan(plan_b_dict)

    logger.info(
        "Comparing flight plans (%d vs %d waypoints)",
        len(plan_a_dict.get("waypoints", [])),
        len(plan_b_dict.get("waypoints", [])),
    )

    client = ChatClient(phase="verify-compare")
    prompt = PLAN_COMPARISON_PROMPT.format(plan_a=plan_a_text, plan_b=plan_b_text)

    response_text = ""
    for result in client.stream_message(
        messages=[{"role": "user", "content": prompt}],
        system=PLAN_COMPARISON_SYSTEM,
        max_tokens=1024,
    ):
        if isinstance(result, StreamChunk):
            response_text += result.text
            if verbose:
                print(result.text, end="", flush=True)

    if verbose:
        print()

    data = _parse_comparison_response(response_text)

    return ComparisonResult(
        verdict=ComparisonVerdict(data.get("verdict", "uncertain")),
        confidence=float(data.get("confidence", 0.0)),
        rationale=data.get("rationale", ""),
        differences=data.get("differences", []),
        artifact_type="plan",
    )
