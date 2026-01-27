# Runtime Architecture (Module-Level)

This document maps the runtime flow of the app and shows how screens call into
the orchestration layer and supporting modules.

## Entry Points

- `src/waypoints/main.py` is the CLI entry.
  - Default: launches TUI (`WaypointsApp`).
  - Subcommands: `export`, `import`, `run`, `compare`, `verify`.

## High-Level Runtime Flow

```text
main.py
  -> tui/app.py (WaypointsApp)
     -> screen routing and resume logic
     -> TUI screens (ideation, qa, brief, spec, chart, fly, land)
        -> JourneyCoordinator (business logic)
           -> models/* (Project, Journey, FlightPlan, Dialogue, Session)
           -> llm/* (ChatClient + prompts + validation)
           -> fly/* (execution, logs, interventions)
           -> git/* (optional commits/tags)
```

## Module-Level Runtime Diagram

```text
┌────────────────────────────┐
│ src/waypoints/main.py      │
│ CLI entry + command router │
└──────────────┬─────────────┘
               │
      ┌────────┴─────────┐
      │                  │
      │ default (TUI)    │ subcommands (export/import/run/verify/compare)
      │                  │
┌─────▼──────────────────────┐          ┌─────────────────────────────────┐
│ src/waypoints/tui/app.py   │          │ CLI handlers in main.py         │
│ Textual App + screen nav   │          │ - genspec export/import         │
└─────┬──────────────────────┘          │ - run (headless)                │
      │                                  │ - verify/compare               │
      │                                  └──────────────┬─────────────────┘
      │                                                 │
┌─────▼──────────────────────┐                          │
│ TUI Screens (phase UIs)    │                          │
│ src/waypoints/tui/screens/ │                          │
│ ideation/brief/spec/chart/ │                          │
│ fly/land                   │                          │
└─────┬──────────────────────┘                          │
      │ uses                                        uses
      │                                             │
┌─────▼─────────────────────────────────────────────▼─────┐
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
┌──────────────────────────┐               │ providers/*         │
│ .waypoints/ JSON/JSONL   │               └─────────────────────┘
│ (project state + logs)   │
└──────────────────────────┘

FLY execution path (from coordinator):
┌─────────────────────────────────────────────────────┐
│ src/waypoints/fly/executor.py                         │
│ WaypointExecutor                                      │
└─────┬────────────────────────────────────────────────┘
      │ uses
      ▼
┌──────────────────────────┐    ┌──────────────────────┐
│ fly/execution_log.py     │    │ git/service.py       │
│ receipts + audit trail   │    │ commits/tags         │
└──────────────────────────┘    └──────────────────────┘

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
  - Uses `JourneyCoordinator` for:
    - journey transitions (`FLY_READY`, `FLY_EXECUTING`, `FLY_PAUSED`, `FLY_INTERVENTION`, `LAND_REVIEW`)
    - saving flight plan
    - parent completion checks (`check_parent_completion`)
  - Uses `WaypointExecutor` directly for execution loop and logs.
  - Intervention handling:
    - transitions state and shows `InterventionModal`
    - uses coordinator for some waypoint status transitions and persistence

### LAND (Completion Hub)

- `src/waypoints/tui/screens/land.py`
  - Uses coordinator for:
    - transitions back to `FLY_READY` (fix issues)
    - transitions to `SPARK_IDLE` (new iteration)
  - Uses genspec export (`export_project`, `export_to_file`) for spec view/export.

## Headless Flow (CLI `run`)

- `src/waypoints/main.py::cmd_run`
  - Loads project and flight plan
  - Creates `JourneyCoordinator`
  - Loop:
    - `select_next_waypoint(...)`
    - `execute_waypoint(...)` (via coordinator)
    - `handle_execution_result(...)`
  - Intervention errors surface to CLI and follow `--on-error` policy

## Notes on Ownership

- `JourneyCoordinator` owns most business logic and persistence for SHAPE/CHART/FLY.
- `FlyScreen` still orchestrates execution directly via `WaypointExecutor`; this is
  the largest remaining place where UI and business logic are interleaved.
- `WaypointsApp` controls resume routing by journey phase and loads docs/plan
  from disk before entering screens.
