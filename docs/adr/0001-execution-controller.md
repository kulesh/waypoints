# ADR 0001: Extract Execution Controller

Date: 2026-02-05
Status: Accepted

## Context

The FLY phase mixed UI, orchestration, execution, and state transitions inside
`src/waypoints/tui/screens/fly.py`. This coupling made the execution flow harder
to test, reason about, and evolve. A dedicated orchestration boundary was
needed to align with the “bicycle” philosophy and centralize execution logic.

## Decision

Introduce `ExecutionController` in `src/waypoints/orchestration/` to own:
- Execution state transitions
- Waypoint selection and sequencing
- Result handling and intervention flow

Move `ExecutionState` into `src/waypoints/fly/state.py` to make it a shared
execution concept rather than a UI-local enum.

## Consequences

- FLY screen becomes thinner and more focused on UI concerns.
- Execution logic is testable in isolation with unit tests.
- Additional orchestration features (rollback, richer reports) have a clear
  home without bloating the UI layer.
