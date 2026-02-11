# Current Implementation Snapshot (2026-02-11)

This document is the runtime truth source for what is implemented today.
Use it alongside:

- `README.md` for onboarding and operator workflow
- `docs/runtime-architecture.md` for module ownership and boundaries
- `docs/testing-strategy.md` for validation strategy and roadmap

## Product Phases

| Phase | Status | Notes |
|-------|--------|-------|
| SPARK | Implemented | Idea entry and project creation |
| SHAPE | Implemented | Q&A, brief generation/review, product spec generation/review |
| CHART | Implemented | Flight plan generation and editing operations |
| FLY | Implemented | Headless + TUI waypoint execution loop with intervention handling |
| LAND | Implemented | Debrief/ship/iterate/gen-spec workflow screens |

## Validation and Quality Gates

- Local quality gates are active and passing: `pytest`, `ruff`, `mypy`.
- CI enforces lint, format, type checking, architecture guard tests, and the full test suite.
- Architecture guard tests currently enforce:
  - thin `main.py` and CLI separation contracts
  - fly screen private-boundary restrictions
  - cross-screen/widget private API call bans for key TUI boundaries

## Flight Test Coverage

| Level | Status | Artifacts |
|-------|--------|-----------|
| L0 | Implemented | `flight-tests/L0-hello-world` |
| L1 | Implemented | `flight-tests/L1-todo-cli` |
| L2-L4 | Planned | Reserved in strategy docs; not yet checked in |
| L5 (self-host) | Harness available | `flight-tests/self-host/` (manual/semi-automatic flow) |

## Intentional Gaps (Tracked)

- Large-module decomposition work remains in progress for several TUI/execution files.
- Quality-gate rigor is stronger than scenario acceptance coverage in advanced flight-test levels.
- Some roadmap items in strategy/spec docs are still forward-looking and should be read as planned work, not shipped behavior.

## Decomposition Progress (Wave 2)

- `src/waypoints/tui/widgets/flight_plan.py` style payload extracted to
  `src/waypoints/tui/widgets/flight_plan_styles.py` and module-size guardrails
  tightened.
- `src/waypoints/fly/executor.py` core execution types extracted to
  `src/waypoints/fly/types.py` with compatibility exports preserved.
- `src/waypoints/tui/screens/fly.py` runtime helpers extracted to
  `src/waypoints/tui/screens/fly_runtime.py`.
