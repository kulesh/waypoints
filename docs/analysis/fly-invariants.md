# FLY Invariants (2026-02-05)

These invariants define expected behavior in FLY execution. They should be preserved during refactor and enforced through tests.

## State and Transition Invariants

- `JourneyCoordinator.transition(...)` is the single source of truth for journey state transitions.
- `ExecutionState` is a UI execution mode, but must be consistent with `JourneyState`:
  - `ExecutionState.RUNNING` implies `JourneyState.FLY_EXECUTING`.
  - `ExecutionState.PAUSED` implies `JourneyState.FLY_PAUSED`.
  - `ExecutionState.INTERVENTION` implies `JourneyState.FLY_INTERVENTION`.
  - `ExecutionState.DONE` implies all waypoints complete and `JourneyState.LAND_REVIEW` is reachable.
- Non-recoverable states should not be persisted as resume checkpoints.

## Waypoint Status Invariants

- When execution starts, current waypoint becomes `IN_PROGRESS`.
- On success, waypoint must be marked `COMPLETE`, persisted, and logged.
- On intervention or failure, waypoint must be marked `FAILED` (or `SKIPPED` for explicit skips).
- Parent epic completion is checked after a child completes, but epics are not auto-completed.

## Selection Invariants

- Selection prefers resumable waypoints (`IN_PROGRESS`, `FAILED`) when resuming.
- Selection should not allow a waypoint whose dependencies are incomplete.
- Epics become eligible only when all children complete.

## Execution Invariants

- Execution uses `WaypointExecutor` exclusively.
- UI must remain responsive (execution runs in background worker).
- Progress updates are handled on main thread via `call_later`.
- `ExecutionResult` drives state transitions; no silent fall-through.

## Logging and Metrics Invariants

- Each waypoint execution produces an execution log.
- Cost and token metrics are updated after each waypoint.
- Receipt validation must occur before auto-commit.

## Recovery Invariants

- Stale `IN_PROGRESS` waypoints are reset to `PENDING` on screen mount.
- Intervention must surface a modal with explicit user action choices.
- Rollback is best-effort and must not corrupt the flight plan state.
