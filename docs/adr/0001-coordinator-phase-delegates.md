# ADR-0001: Coordinator as Facade with Phase Delegates

## Context

`coordinator.py` grew to 1,501 lines mixing four unrelated phase concerns
(FLY execution, CHART planning, SHAPE ideation, shared state). Screens
diverged from the coordinator, reimplementing business logic locally —
particularly status mutations and git commit handling in `FlyScreen`.

## Decision

Coordinator becomes a thin facade (~340 lines) delegating to `FlyPhase`,
`ChartPhase`, and `ShapePhase`. Each delegate receives a reference to the
coordinator (the coordinator IS the shared context). The public API is
unchanged — callers still call `coordinator.execute_waypoint(...)`, which
routes to `self._fly.execute_waypoint(...)`.

## Alternatives Considered

- **Mixins** — share state via multiple inheritance. Creates diamond problems
  and hides which mixin owns what state/behavior.
- **Separate coordinator instances per phase** — clean isolation, but
  duplicates shared state (flight_plan, project, git) and requires
  synchronization between instances.
- **Extract to standalone functions** — loses the shared-state benefit
  entirely; every function needs the full context passed in.
- **Keep single class, reorganize with regions** — doesn't address
  testability or the boundary violations that caused screen divergence.

## Consequences

- 19 of 32 coordinator methods are one-line delegations (clear routing).
- Phase classes are independently testable with a mock coordinator.
- Circular imports avoided via `TYPE_CHECKING` guard on coordinator import.
- Trade-off: delegates access 5–7 coordinator attributes each, creating
  implicit coupling. Mitigated by keeping the attribute set small and stable.
