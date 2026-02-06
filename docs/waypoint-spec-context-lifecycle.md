# Waypoint Spec Context Lifecycle

This document describes how waypoint-level spec context is generated, consumed,
and backfilled.

## Purpose

Each waypoint carries three fields sourced from the product spec:

- `spec_context_summary`: waypoint-scoped summary of relevant requirements
- `spec_section_refs`: explicit section headings from the product spec
- `spec_context_hash`: hash of the product spec version used to generate context

These fields reduce fly-time prompt size while preserving traceability to the
full product spec.

## Lifecycle

1. **Chart generation**
   - Waypoint generation produces summary + section refs.
   - Runtime computes and stores `spec_context_hash` from the current spec text.
2. **Fly execution**
   - Fly prompts use waypoint summary + refs instead of inlining spec body.
   - Prompt includes pointer to canonical spec file: `docs/product-spec.md`.
   - Executor compares stored waypoint hash to current spec hash and marks
     context as stale if they differ.
3. **Operational refresh**
   - Existing projects (or stale waypoints after spec edits) can be refreshed
     via CLI command.

## CLI Operations

Regenerate for one project:

```bash
uv run waypoints memory refresh-spec-context <project-slug>
```

Regenerate for all projects:

```bash
uv run waypoints memory refresh-spec-context --all
```

Refresh only stale/missing contexts:

```bash
uv run waypoints memory refresh-spec-context <project-slug> --only-stale
```

The command updates `flight-plan.jsonl` in place when waypoint context changes.

## Backfill Behavior

- Uses deterministic markdown section relevance scoring (no LLM calls)
- Selects the most relevant section headings for each waypoint objective and
  acceptance criteria
- Generates bounded summary text suitable for fly-time prompt consumption
- Preserves unchanged waypoints in `--only-stale` mode

## Recommended Usage

- Run `refresh-spec-context` after major product spec edits.
- Prefer `--only-stale` for regular maintenance.
- Use full refresh during migrations or when introducing new summary heuristics.
