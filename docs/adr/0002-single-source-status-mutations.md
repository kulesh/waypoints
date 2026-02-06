# ADR-0002: Single Source of Truth for Status Mutations

## Context

After the facade refactoring (ADR-0001), SUCCESS results flowed through the
coordinator for status updates, but FAILED / MAX_ITERATIONS /
INTERVENTION_NEEDED bypassed it via `_mark_waypoint_failed()` in FlyScreen.
Headless callers (`main.py`, `run_fly.py`) also skipped status persistence
for non-success results. Two mutation paths meant divergent behavior between
TUI and headless modes.

## Decision

All five `ExecutionResult` types flow through
`fly_phase.handle_execution_result()`. The coordinator is the ONLY place that
mutates waypoint status. `FlyScreen` dispatches on the returned `NextAction`
(not on `ExecutionResult`) — it renders outcomes, it doesn't decide them.
Exception paths (`InterventionNeededError`) use
`coordinator.mark_waypoint_status()` directly.

| Result               | Status Set | Action Returned  |
|----------------------|------------|------------------|
| SUCCESS              | COMPLETE   | continue/complete|
| FAILED               | FAILED     | intervention     |
| MAX_ITERATIONS       | FAILED     | intervention     |
| INTERVENTION_NEEDED  | FAILED     | intervention     |
| CANCELLED            | PENDING    | pause            |

## Alternatives Considered

- **Keep `_mark_waypoint_failed` but call coordinator internally** — adds a
  layer without solving the root problem (screen still decides when to call it).
- **Event-based pattern with status change events** — over-engineered for the
  current scale; adds indirection without solving a real problem.
- **Status mutation in executor instead of coordinator** — wrong boundary;
  the executor doesn't own journey lifecycle.

## Consequences

- `_mark_waypoint_failed` deleted from FlyScreen.
- FlyScreen's `_handle_execution_result` shrinks; dispatches on `NextAction.action`.
- All callers (TUI, `main.py`, `run_fly.py`) route through coordinator.
- The `else` branch bug (MAX_ITERATIONS / INTERVENTION_NEEDED never set status)
  is fixed as a side effect.
