"""Clarification protocol runtime helpers for FLY execution."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from waypoints.fly.protocol import ClarificationRequest, ClarificationResponse, FlyRole
from waypoints.fly.types import _LoopState

MAX_CLARIFICATION_ROUNDS = 2
_CLARIFICATION_REQUEST_PATTERN = re.compile(
    r"<clarification-request>\s*(\{.*?\})\s*</clarification-request>",
    re.DOTALL,
)


def extract_clarification_payloads(text: str) -> list[dict[str, Any]]:
    """Extract structured clarification payloads from model output tags."""
    payloads: list[dict[str, Any]] = []
    for match in _CLARIFICATION_REQUEST_PATTERN.findall(text):
        try:
            parsed = json.loads(match)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def build_clarification_response(
    request: ClarificationRequest,
) -> ClarificationResponse:
    """Generate deterministic orchestrator response for clarification."""
    option = (
        request.requested_options[0]
        if request.requested_options
        else "Follow docs/product-spec.md and existing repository conventions."
    )
    return ClarificationResponse(
        waypoint_id=request.waypoint_id,
        produced_by_role=FlyRole.ORCHESTRATOR,
        source_refs=("docs/product-spec.md", "AGENTS.md"),
        request_artifact_id=request.artifact_id,
        chosen_option=option,
        rationale=(
            "Canonical source precedence applies: product spec and development "
            "covenant override ambiguous secondary hints."
        ),
        updated_constraints=(
            "Prefer docs/product-spec.md when requirement wording conflicts.",
            "Escalate again if ambiguity remains after applying this rule.",
        ),
    )


def handle_clarification_requests(
    state: _LoopState,
    *,
    waypoint_id: str,
    log_artifact: Callable[[object], None],
) -> None:
    """Capture clarification requests and emit orchestrator responses."""
    for payload in extract_clarification_payloads(state.full_output):
        signature = json.dumps(payload, sort_keys=True)
        if signature in state.clarification_signatures:
            continue
        state.clarification_signatures.add(signature)

        question = str(
            payload.get("question") or payload.get("blocking_question") or ""
        )
        context = str(payload.get("context") or payload.get("decision_context", ""))
        raw_confidence = payload.get("confidence", payload.get("confidence_level", 0))
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        options_raw = payload.get("options") or payload.get("requested_options")
        options: tuple[str, ...] = ()
        if isinstance(options_raw, list):
            options = tuple(str(item) for item in options_raw if str(item).strip())

        request = ClarificationRequest(
            waypoint_id=waypoint_id,
            produced_by_role=FlyRole.BUILDER,
            source_refs=("execution-output",),
            blocking_question=question,
            decision_context=context,
            confidence_level=confidence,
            requested_options=options,
        )
        log_artifact(request)

        state.clarification_rounds += 1
        state.unresolved_clarification = True
        if state.clarification_rounds > MAX_CLARIFICATION_ROUNDS:
            state.clarification_exhausted = True
            state.next_reason_code = "clarification_budget_exhausted"
            state.next_reason_detail = (
                "Clarification rounds exhausted. Escalate to intervention."
            )
            continue

        response = build_clarification_response(request)
        log_artifact(response)
        state.unresolved_clarification = False
        state.next_reason_code = "clarification_resolved"
        state.next_reason_detail = (
            "Clarification resolved by orchestrator guidance; continue with "
            "explicit policy constraints."
        )
