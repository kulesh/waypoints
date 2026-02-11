# Fly/Main Decomposition Plan (2026-02-11)

## Goal

Eliminate recurring complexity regressions in:

- `src/waypoints/tui/screens/fly.py`
- `src/waypoints/main.py`

without changing user-visible behavior.

This plan defines the ideal end-state first, then a phased migration path with hard gates.

## Why This Keeps Regressing

Past refactors extracted helpers, but not the core control-flow ownership. `FlyScreen` still owns:

- workflow decisions
- journey transitions
- worker lifecycle
- intervention routing
- timer lifecycle
- detailed rendering and log formatting

Concrete coupling evidence in current code:

- Private coordinator access: `src/waypoints/tui/screens/fly.py:2301` (`self.coordinator._fly.active_executor`)
- Private app API access: `src/waypoints/tui/screens/fly.py:2222`
- Private metrics internals: `src/waypoints/tui/screens/fly.py:1646`
- Large mixed responsibilities across one class: `src/waypoints/tui/screens/fly.py:1458`

`main.py` has a parallel problem: parser definition, command routing, and command implementations are all co-located (`src/waypoints/main.py:42`, `src/waypoints/main.py:269`, `src/waypoints/main.py:646`).

## Ideal End-State

### A. Fly architecture

`FlyScreen` becomes a thin adapter from Textual events to application services.

Target package:

```text
src/waypoints/tui/screens/fly/
  __init__.py
  screen.py                 # Textual widget wiring only
  session.py                # FlySession dataclass (UI state)
  controller.py             # UI-agnostic orchestration adapter
  actions.py                # action handlers (start/pause/skip/debug/back/forward)
  progress.py               # progress/event mapping from executor to view model
  intervention.py           # intervention flow mapping
  status.py                 # state/status line derivation
  timers.py                 # timer lifecycle policy (ticker + budget wait)
```

Widget/log rendering helpers move out of screen:

```text
src/waypoints/tui/widgets/
  fly_execution_log.py
  fly_detail_panel.py
  fly_waypoint_list_panel.py
```

`src/waypoints/tui/screens/fly.py` becomes a compatibility shim that re-exports from `tui/screens/fly/screen.py` during migration.

#### Fly ownership rules

- `FlyScreen`: layout, bindings, widget querying, notifications, modal push/switch only.
- `FlyController`: execution flow decisions, state transitions, next action dispatch.
- `FlySession`: current waypoint, execution state, timers, budget wait state.
- `JourneyCoordinator/FlyPhase`: domain mutations and persistence.
- Log/panel widgets: formatting only, no workflow decisions.

#### Fly hard invariants

- No `._` private access across component boundaries.
- No direct `JourneyState` transition branching in screen methods.
- No business branching in widget classes.

### B. CLI architecture

Create `cli` package with explicit boundaries:

```text
src/waypoints/cli/
  __init__.py
  app.py                    # run(argv) orchestration
  parser.py                 # argparse construction only
  context.py                # shared context helpers (paths, project lookup)
  commands/
    __init__.py
    export.py
    import_cmd.py
    run.py
    compare.py
    verify.py
    view.py
    memory.py
```

`src/waypoints/main.py` becomes a stable thin entrypoint:

- setup logging
- call `cli.app.run(sys.argv[1:])`
- `sys.exit(code)`

Runner modules (`src/waypoints/runners/run_*.py`) consume shared command services where possible to remove duplicated headless behavior.

#### CLI hard invariants

- Parser module has no command business logic.
- Command modules have no argparse construction.
- Command handlers share project-loading and error mapping utilities.

## Quantitative End-State Targets

- `src/waypoints/tui/screens/fly/screen.py`: <= 450 lines
- No single fly helper module > 350 lines
- `src/waypoints/main.py`: <= 120 lines
- `FlyScreen` direct methods reduced to UI lifecycle + bindings only
- `mypy`, `ruff check`, `ruff format --check`, `pytest` all green at every phase

## Implementation Strategy (No Big-Bang)

Use a strangler approach: extract one seam at a time, preserve behavior, then delete old code.

### Phase 0: Lock Current Behavior

1. Add high-value behavior tests for current fly and CLI flows before extraction.
2. Record state-transition expectations as tests (not prose).
3. Add regression tests for known sensitive paths:
   - budget wait auto-resume
   - intervention retry/rollback/edit/wait branches
   - run command `--on-error` variants

Exit criteria:

- New tests fail if branching behavior changes.

### Phase 1: UI Component Extraction (Low Risk)

1. Move `ExecutionLog`, `AcceptanceCriteriaList`, `WaypointDetailPanel`, `WaypointListPanel` into widget modules.
2. Keep interfaces unchanged.
3. Leave `FlyScreen` logic intact.

Exit criteria:

- Zero behavior change.
- `FlyScreen` shrinks materially from widget code removal.

### Phase 2: Introduce `FlySession` + `status/timers` Modules

1. Create `FlySession` dataclass for execution/timer/budget state.
2. Move status derivation and countdown/timer transitions into `status.py` and `timers.py`.
3. Replace ad-hoc screen fields with session state object.

Exit criteria:

- Timer lifecycle logic fully out of `FlyScreen` methods except callbacks.
- No direct mutation of timer fields outside session/timer module.

### Phase 3: Extract Execution Controller

1. Create `FlyController` with explicit commands:
   - `start()`
   - `pause()`
   - `handle_worker_result(...)`
   - `handle_intervention_result(...)`
2. Move branching currently in:
   - `action_start`
   - `_handle_execution_result`
   - `_handle_intervention`
   - `_on_intervention_result`
3. Screen forwards UI intents and applies returned view actions.

Exit criteria:

- `FlyScreen` no longer decides journey transitions.
- `FlyScreen` no longer branches on `ExecutionResult`/`InterventionAction` semantics.

### Phase 4: Remove Private Boundary Violations

1. Add coordinator/public APIs needed by screen/controller:
   - active executor getter usage instead of `coordinator._fly.active_executor`
   - app doc loading API instead of `app._load_latest_doc`
   - metrics summary API instead of `metrics_collector._calls`
2. Replace all direct private access in fly path.

Exit criteria:

- No `._` boundary violations in `src/waypoints/tui/screens/fly*`.

### Phase 5: CLI Modularization

1. Move parser construction to `cli/parser.py`.
2. Move each command handler to `cli/commands/*`.
3. Introduce shared helpers in `cli/context.py` for project/file loading and error prints.
4. Reduce `main.py` to thin entrypoint + logging.

Exit criteria:

- Command-level tests cover command modules directly.
- `main.py` only bootstraps and routes.

### Phase 6: Convergence and Cleanup

1. Compare `main.py cmd_run` and `runners/run_fly.py`; unify shared execution service.
2. Remove duplicate logic paths where possible.
3. Update docs (`runtime-architecture.md`) to reflect actual boundaries.
4. Add ADR for final Fly screen boundary contract.

Exit criteria:

- One authoritative headless execution path.
- Architecture docs match implementation.

## Testing Plan by Phase

- Unit tests:
  - controller decisions
  - timer/status derivation
  - CLI command modules
- Integration tests:
  - `FlyScreen` with mocked controller outcomes
  - end-to-end CLI command behavior
- Golden behavior tests:
  - intervention and budget wait transitions
  - `run --on-error` behavior

Rule: each extraction phase must add or preserve tests before deleting old logic.

## Change Management and Risk Controls

- Keep old import paths via temporary shims until call sites migrate.
- Land small PRs per phase with explicit "no behavior change" intent.
- Enforce size budgets in CI (soft gate first, hard gate after migration).
- Track each phase as separate beads issue with dependencies.

## Recommended Issue Breakdown (Beads)

1. `fly-refactor-phase0-behavior-lock`
2. `fly-refactor-phase1-widget-extraction`
3. `fly-refactor-phase2-session-status-timers`
4. `fly-refactor-phase3-controller-extraction`
5. `fly-refactor-phase4-private-boundary-cleanup`
6. `cli-refactor-phase5-command-modularization`
7. `refactor-phase6-convergence-doc-sync`

## Definition of Done for #3

- Fly and CLI complexity is structurally bounded by module ownership, not just line splits.
- Screen and entrypoint files remain thin and stable under feature growth.
- Regression tests prove migration preserved behavior.
- Architecture docs and ADRs reflect reality.
