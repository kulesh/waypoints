# Project Memory Index

Waypoints persists project-level memory in each target project under:

```text
<project-root>/.waypoints/memory/
```

This memory is generated from filesystem and manifest signals, then reused by both:

- prompt guidance (what to prioritize vs ignore)
- tool guardrails (which top-level directories are blocked)

## Files

- `stack-profile.v1.json`
  - detected stack signals (`python`, `typescript`, `rust`, etc.)
  - discovered manifest paths (`pyproject.toml`, `package.json`, `Cargo.toml`, ...)
- `directory-map.v1.json`
  - classified top-level entries with roles (`source`, `tests`, `dependency`, `cache`, ...)
  - per-entry flags: `ignore_for_search`, `blocked_for_tools`, plus reason
- `project-index.v1.json`
  - compact runtime policy:
  - `blocked_top_level_dirs`
  - `ignored_top_level_dirs`
  - `focus_top_level_dirs`
  - `top_level_fingerprint` for stale-index detection
- `waypoint/*.json`
  - one file per waypoint execution outcome
  - includes result, iterations used, key changed files, validation commands,
    protocol derailments, and verified criteria
- `policy-overrides.v1.json`
  - optional project-authored top-level policy adjustments
  - keys: `block_dirs`, `unblock_dirs`, `ignore_dirs`, `unignore_dirs`, `focus_dirs`

## Generation and Refresh

When fly execution starts (and when tools need policy), Waypoints:

1. attempts to load memory files
2. computes top-level fingerprint
3. rebuilds memory if files are missing/invalid/stale
4. writes refreshed files back to `.waypoints/memory/`

This keeps policy adaptive as the project evolves.

Manual refresh:

```bash
waypoints memory refresh <project-slug>
waypoints memory refresh --all
waypoints memory refresh <project-slug> --init-overrides
```

## Safety Model

There are immutable safety boundaries that are always blocked:

- `.git`
- `.waypoints`
- `sessions`
- `receipts`

Stack/generation-specific directories are added dynamically based on detected signals and directory classification.
Policy overrides cannot remove immutable safety boundaries.

## Prompt + Tool Alignment

The same `project-index.v1.json` policy is used in two places:

1. **Prompt context**: compact directory policy summary is injected into fly prompts/system prompt.
2. **Tool enforcement**: `Read`/`Write`/`Edit`/`Glob`/`Grep` path checks deny blocked roots.

This avoids drift between “what the model is told” and “what the runtime enforces”.

Waypoint memory is also injected into new waypoint execution prompts as a compact,
token-bounded context block. Selection priority:

1. dependency waypoint memory
2. lexically similar recent waypoint memory

## Next Step

Extend `.waypoints/memory/` with waypoint-scoped memory (for example, solved pitfalls and validated command recipes) and surface a compact retrieval summary to new waypoints.
