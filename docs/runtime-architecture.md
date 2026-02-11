# Runtime Architecture (Module-Level)

This document maps the runtime flow of the app and shows how screens call into
the orchestration layer and supporting modules.

## Entry Points

- `src/waypoints/main.py` is a thin entrypoint.
  - Configures logging and delegates to `src/waypoints/cli/app.py::run`.
- `src/waypoints/cli/parser.py` owns argparse construction.
- `src/waypoints/cli/commands/*` owns command handlers.
  - `export`, `import`, `run`, `compare`, `verify`, `view`, `memory`.

## High-Level Runtime Flow

```text
main.py
  -> cli/app.py (argv orchestration + command routing)
     -> cli/commands/* (command handlers)
        -> JourneyCoordinator (business logic)
           -> models/* (Project, Journey, FlightPlan, Dialogue, Session)
           -> llm/* (ChatClient + prompts + validation)
           -> fly/* (execution, logs, interventions)
           -> runtime/* (command runner + timeout policy)
           -> git/* (optional commits/tags)
  -> tui/app.py (for default TUI command)
     -> screen routing and resume logic
     -> TUI screens (ideation, qa, brief, spec, chart, fly, land)
```

## Module-Level Runtime Diagram

```text
┌──────────────────────────────────────┐
│ src/waypoints/main.py                │
│ thin entrypoint + logging bootstrap  │
└──────────────────┬───────────────────┘
                   │
          ┌────────▼────────┐
          │ cli/app.py      │
          │ parse + route   │
          └──────┬──────────┘
                 │
      ┌──────────┴───────────┐
      │                      │
      │ default (TUI)        │ subcommands (export/import/run/verify/...)
      │                      │
┌─────▼──────────────────────┐    ┌────────────────────────────────────────┐
│ src/waypoints/tui/app.py   │    │ src/waypoints/cli/commands/*          │
│ Textual App + screen nav   │    │ command business logic (no argparse)  │
└─────┬──────────────────────┘    └──────────────────────┬─────────────────┘
      │                                                  │
┌─────▼──────────────────────┐                           │
│ TUI Screens (phase UIs)    │                           │
│ src/waypoints/tui/screens/ │                           │
│ ideation/brief/spec/chart/ │                           │
│ fly/land                   │                           │
└─────┬──────────────────────┘                           │
      │ uses                                         uses
      │                                              │
┌─────▼──────────────────────────────────────────────▼─────┐
│ src/waypoints/orchestration/coordinator.py             │
│ JourneyCoordinator: business logic + state transitions │
└─────┬─────────────────────────────────────────────┬─────┘
      │                                             │
      │ uses                                        │ uses
      ▼                                             ▼
┌───────────────┐                          ┌─────────────────────┐
│ Models + IO   │                          │ LLM layer           │
│ src/waypoints │                          │ src/waypoints/llm/  │
│ /models/*     │                          │ client/prompts/     │
│ - Project     │                          │ tools/validation    │
│ - Journey     │                          └─────────┬───────────┘
│ - FlightPlan  │                                    │
│ - Dialogue    │                              calls providers
│ - Session     │                                    │
└─────┬─────────┘                          ┌─────────▼───────────┐
      │ persist/load                       │ LLM providers       │
      ▼                                    │ src/waypoints/llm/  │
┌──────────────────────────────────────────┐               │ providers/*         │
│ <projects-root>/<slug>/ JSON/JSONL       │               └─────────────────────┘
│ (project state + logs, configurable root)│
└──────────────────────────────────────────┘

FLY execution path (from coordinator):
┌──────────────────────────────────────────────────────────────┐
│ src/waypoints/orchestration/fly_phase.py                     │
│ FlyPhase: executor lifecycle, intervention, budget, routing  │
└─────┬────────────────────────────────────────────────────────┘
      │ delegates execution + git policy
      ▼
┌─────────────────────────────┐   ┌────────────────────────────────┐
│ src/waypoints/fly/executor.py│   │ src/waypoints/orchestration/   │
│ WaypointExecutor             │   │ fly_git.py                      │
│ + intervention_policy.py     │   │ Commit/Rollback policy          │
│ + escalation_policy.py       │   └────────────────────────────────┘
└─────┬───────────────────────┘
      │ uses
      ▼
┌──────────────────────────┐
│ fly/execution_log.py     │
│ receipts + audit trail   │
└──────────────────────────┘

Genspec + verification paths (CLI + TUI export):
┌─────────────────────────────┐  ┌─────────────────────────────┐
│ genspec/exporter.py         │  │ genspec/importer.py         │
│ genspec/spec.py             │  │ verify/orchestrator.py      │
└─────────────────────────────┘  └─────────────────────────────┘
```

## Screen to Coordinator Call Map (by phase)

### SPARK

- `src/waypoints/tui/screens/ideation.py`
  - Creates project and transitions journey to `spark:entering`.
  - Calls `JourneyCoordinator.transition(...)` only.

### SHAPE (Q&A)

- `src/waypoints/tui/screens/ideation_qa.py`
  - On mount:
    - `transition(SHAPE_QA)` (spark entering -> shape qa)
    - `start_qa_dialogue(...)`
  - On user response:
    - `continue_qa_dialogue(...)`
  - On finish:
    - switches to Idea Brief screen (passes `dialogue_history`)

### SHAPE (Idea Brief)

- `src/waypoints/tui/screens/idea_brief.py`
  - On mount:
    - `transition(SHAPE_BRIEF_GENERATING)`
    - `generate_idea_brief(...)` (streams + saves)
  - On finalize:
    - `transition(SHAPE_BRIEF_REVIEW)`
  - On proceed:
    - switches to Product Spec screen

### SHAPE (Product Spec)

- `src/waypoints/tui/screens/product_spec.py`
  - On mount:
    - `transition(SHAPE_SPEC_GENERATING)`
    - `generate_product_spec(...)` (streams + saves)
  - On finalize:
    - `transition(SHAPE_SPEC_REVIEW)`
  - On proceed:
    - switches to Chart screen

### CHART (Flight Plan)

- `src/waypoints/tui/screens/chart.py`
  - On mount (no existing plan):
    - `transition(CHART_GENERATING)`
    - `generate_flight_plan(...)`
    - `transition(CHART_REVIEW)`
  - Editing:
    - `update_waypoint(...)`
    - `add_waypoint(...)`
    - `delete_waypoint(...)`
    - `add_sub_waypoints(...)`
    - `reorder_waypoints(...)`
  - AI assists:
    - `generate_waypoint(...)`
    - `break_down_waypoint(...)`
    - `suggest_reprioritization(...)`
  - Proceed to Fly:
    - `transition(FLY_READY)` then `switch_phase("fly")`

### FLY (Execution)

- `src/waypoints/tui/screens/fly.py`
  - UI adapter layer + timer/session wiring.
  - Consumes live `metrics_updated` execution progress payloads to refresh:
    - waypoint cost/token/cached metrics in the detail panel
    - project-wide cost/token/cached metrics in the waypoint list panel
  - Uses `src/waypoints/tui/screens/fly_controller.py` for execution/intervention
    branching decisions.
  - Delegates all business logic to `JourneyCoordinator` / `FlyPhase`:
    - journey transitions (`FLY_READY`, `FLY_EXECUTING`, `FLY_PAUSED`, `FLY_INTERVENTION`, `LAND_REVIEW`)
    - executor lifecycle (`create_executor`, `cancel_execution`, `execute_waypoint`)
    - waypoint status mutations (`mark_waypoint_status`)
    - intervention classification and state (`classify_intervention`, `handle_intervention`)
    - budget wait computation (`compute_budget_wait`)
    - git rollback (`rollback_to_ref`) and commit (`commit_waypoint`) via `fly_git.py`
    - execution logging (`log_pause`, `log_git_commit`, `log_intervention_resolved`)
      via public `WaypointExecutor` logging APIs (no private attribute access)
    - flight plan persistence and parent completion checks
  - FlyScreen is a pure UI layer: renders progress, manages timers, shows modals
  - Fly execution metrics data path:
    - provider stream usage -> `fly/executor.py` / `fly/receipt_finalizer.py`
    - progress callback (`ExecutionContext.step == "metrics_updated"`)
    - UI projection/render in Fly screen widgets

### LAND (Completion Hub)

- `src/waypoints/tui/screens/land.py`
  - Uses coordinator for:
    - transitions back to `FLY_READY` (fix issues)
    - transitions to `SPARK_IDLE` (new iteration)
  - Uses genspec export (`export_project`, `export_to_file`) for spec view/export.

## Headless Flow (CLI + Runner)

- `src/waypoints/cli/commands/run.py`
  - User-facing headless command with `--on-error` policy.
- `src/waypoints/runners/run_fly.py`
  - JSONL-oriented runner for scripted pipelines.
- Shared execution path:
  - `src/waypoints/orchestration/headless_fly.py::execute_waypoint_with_coordinator`
  - Normalizes waypoint outcomes:
    - success/failure (result + next action)
    - intervention (status marked failed)
    - unexpected error (status marked failed)

## Notes on Ownership

- `JourneyCoordinator` owns all business logic and persistence for SHAPE/CHART/FLY.
  It delegates to phase-specific classes: `FlyPhase`, `ChartPhase`, `ShapePhase`.
- `FlyPhase` manages executor lifecycle, intervention state, budget wait computation,
  and delegates git policy to `fly_git.py`.
- `WaypointExecutor` delegates error classification to `fly/intervention_policy.py`
  and protocol escalation decisions to `fly/escalation_policy.py`.
- `WaypointsApp` controls resume routing by journey phase and loads docs/plan
  from disk before entering screens.
- `cli/parser.py` is the only argparse owner; `cli/commands/*` modules are
  parser-agnostic command handlers.
- `orchestration/headless_fly.py` is the shared waypoint execution service for
  both CLI run and JSONL runner flows.
