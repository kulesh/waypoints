# ADR-0003: Fly Screen Boundary Contract

## Context

`FlyScreen` repeatedly regressed into a mixed-responsibility class owning:

- Textual widget composition and rendering
- Execution/intervention branching
- Timer lifecycle
- Coordinator and app private API access

This made behavior brittle, inflated `fly.py`, and caused repeated refactor churn
without a stable ownership contract.

## Decision

Adopt an explicit boundary contract for Fly execution:

- `tui/screens/fly.py` is the UI adapter only.
  - Owns Textual events, bindings, notifications, and modal wiring.
- `tui/screens/fly_controller.py` owns execution/intervention branching decisions.
  - Screen forwards intents and applies returned decisions.
- `JourneyCoordinator` / `FlyPhase` own domain mutation and persistence.
  - Status transitions, intervention handling, commit/rollback policy.
- `tui/screens/fly_session.py`, `fly_status.py`, `fly_timers.py` own session/timer
  derivation and lifecycle helpers.
- Widgets own presentation only.

Hard invariants:

1. No cross-component private API access in fly path.
2. Screen does not branch on raw worker exceptions/intervention semantics directly.
3. Screen applies coordinator/controller decisions; it does not infer domain state.

## Alternatives Considered

- **Single-class FlyScreen with helper methods**
  - Simplifies imports but keeps ownership implicit and regressions likely.
- **Push all branching into coordinator**
  - Overloads orchestration with UI-specific concerns (modal and view decisions).
- **Event-bus architecture for fly state**
  - Adds complexity without current-scale payoff.

## Consequences

- Branching logic is testable via focused controller tests.
- Screen-side changes are less likely to alter domain behavior accidentally.
- Public APIs were added where private boundary access previously existed.
- Remaining complexity is now explicitly partitioned, enabling incremental
  migration from a monolithic screen file to smaller modules.
