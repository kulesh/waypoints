# Waypoint Spec Context Checklist

Purpose: move spec summarization to chart-time generation so fly-time execution
uses waypoint-scoped context with section references and a full-spec pointer.

## PR1: Generation-Time Spec Context

- [x] Add waypoint fields: `spec_context_summary`, `spec_section_refs`,
  `spec_context_hash`
- [x] Keep flight-plan JSONL load/save backward compatible
- [x] Add deterministic spec utilities: section index + spec hash
- [x] Update CHART prompts to require waypoint-scoped spec context + refs
- [x] Update validation to enforce spec context on chart outputs
- [x] Validate section refs against parsed spec headings
- [x] Wire fields through:
  - flight plan generation
  - waypoint breakdown
  - manual waypoint add
- [x] Ensure debug-forked waypoints preserve spec context fields
- [x] Add/adjust tests:
  - validation
  - chart retry
  - model serialization

## PR2: Fly Prompt Consumption

- [x] Replace `spec[:2000]` usage with waypoint spec context in fly prompt
- [x] Add full spec pointer (`docs/product-spec.md`) and instruction to read on ambiguity
- [x] Add stale-context detection (`spec_context_hash` vs current spec hash)
- [x] Add logging fields for context usage/staleness
- [x] Add fly prompt + executor logging tests

## PR3: Backfill and Ops

- [x] Add command to regenerate spec context for existing/stale waypoints
- [x] Add docs for lifecycle and operational usage
