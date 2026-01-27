# Unix-Style Multi-UI Architecture Plan

This plan defines a Unix-style architecture for Waypoints that supports multiple
TUIs/platforms via a headless engine and text-first protocols.

## Goals

- Decouple UI from business logic so multiple frontends can coexist.
- Use text-first interfaces (JSON/JSONL) for composability and tooling.
- Make all state changes observable, replayable, and testable.
- Keep persistence simple and inspectable (append-only logs + plain files).

## Non-Goals

- Replacing existing persistence formats immediately.
- Changing product features or UX flows.
- Introducing a networked server by default (stdio-based IPC is primary).

## Target Architecture (Overview)

- Engine (headless core): owns journey state, LLM calls, execution, persistence.
- Protocol: JSON command stream in, JSONL events out.
- UI clients (TUI/Web/IDE): render events, send commands, no direct business logic.

## Milestones

### 1) Engine Boundaries and Invariants

Deliverables:
- Document engine responsibilities and what stays in UI.
- Define authoritative state ownership: engine writes all persistent state.
- Define idempotency rules for commands and how retries are handled.

Acceptance Criteria:
- Clear ownership table exists (engine vs UI).
- State transitions validated in one place (engine).
- Each command has defined side effects and idempotency expectations.

### 2) Persistence Contract and Canonical Sources

Deliverables:
- Inventory of artifacts and logs with canonical vs derived designation.
- Decision on event log as canonical stream of record.
- Versioning approach for each file type (schema headers, migration policy).

Acceptance Criteria:
- Each file has: schema version, owner, and update mechanism.
- Replay process can rebuild derived views from canonical sources.

### 3) Protocol v1 (Commands and Events)

Deliverables:
- Command envelope schema (JSON):
  - command_type, command_id, project_slug, payload, timestamp, schema_version
- Event envelope schema (JSONL):
  - event_type, event_id, command_id, project_slug, payload, timestamp, schema_version
- Error envelope schema with severity and retry guidance.

Command Types (v1):
- start_qa, continue_qa
- generate_brief, generate_spec, generate_plan
- add_waypoint, update_waypoint, delete_waypoint, reorder_waypoints
- execute_waypoint, pause, resume, intervene
- export_genspec, status

Event Types (v1):
- state_changed, dialogue_chunk, dialogue_completed
- artifact_saved, flight_plan_updated
- waypoint_status_changed, execution_log
- metrics_updated, warning, error

Acceptance Criteria:
- Protocol spec is documented and versioned.
- Event stream is append-only and replayable.
- Each command has a defined event sequence.

### 4) Engine CLI and Event Stream

Deliverables:
- `waypoints engine` reads commands from stdin and emits events to stdout.
- Flags: `--project`, `--replay`, `--event-log` (write JSONL), `--quiet`.
- Deterministic replay mode using event log and persisted artifacts.

Acceptance Criteria:
- Engine can run all phases headlessly using commands only.
- Event stream can be captured and replayed to reproduce state.

### 5) UI Bridge (TUI as Client)

Deliverables:
- TUI sends commands and subscribes to event stream.
- Remove direct calls to `WaypointExecutor` and LLM from UI.
- Add lightweight client adapter for command/event routing.

Acceptance Criteria:
- TUI does not import `fly/executor.py` or `llm/client.py` directly.
- Existing UX remains intact with minimal UI changes.

### 6) Migration Sequence

Phase 1: FLY extraction
- Move execution loop into engine.
- Emit execution events; TUI renders only.

Phase 2: CHART and SHAPE extraction
- Move waypoint generation and document generation into engine.
- TUI uses event stream for streaming updates.

Phase 3: Protocol hardening
- Add golden tests for command -> event sequences.
- Add replay and regression tests.

Acceptance Criteria:
- Each phase is runnable via engine CLI without TUI.
- UI regressions are tracked and limited to non-functional issues.

### 7) Testing and Quality Gates

Deliverables:
- Protocol golden tests (fixtures for commands/events).
- Replay determinism tests for each phase.
- Headless E2E tests for spark/shape/chart/fly.

Acceptance Criteria:
- Protocol changes require updating fixtures and version bump.
- Replay produces identical artifacts for stable inputs.

### 8) Rollout and Compatibility

Deliverables:
- Parallel-run mode: UI + engine side-by-side (feature flag).
- Migration guide for existing projects (no data loss).

Acceptance Criteria:
- Old and new paths can coexist until cutover.
- Clear rollback path exists per milestone.

## Open Questions

- Should the event log be canonical or derived from files?
- Do we need a stable, named IPC transport (stdio only vs optional socket)?
- Should the engine own all git operations or expose them as commands?
- How strictly should replay handle non-deterministic LLM outputs?

## Suggested Next Step

- Finalize protocol v1 schema and publish it as `docs/protocol-v1.md`.
