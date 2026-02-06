# FLY Call Graph (2026-02-05)

This document maps the current FLY execution flow from UI actions down to orchestration and execution. The goal is to identify extraction boundaries for a dedicated execution controller.

## Entry Points (User Actions)

- `FlyScreen.on_mount()`
  - `coordinator.reset_stale_in_progress()`
  - `_refresh_waypoint_list()`
  - `_select_next_waypoint(include_in_progress=True)`
  - `_update_git_status()` + timer
  - `_update_project_metrics()`

- `action_start()`
  - Handles retry of selected failed waypoint
  - Handles resume from `PAUSED`
  - Handles start from `READY` or after `CHART_REVIEW` / `LAND_REVIEW`
  - Transitions via `coordinator.transition(...)`
  - Sets `execution_state = RUNNING`
  - `_execute_current_waypoint()`

- `action_pause()`
  - Sets `execution_state = PAUSE_PENDING`
  - Cancels executor if running (logs pause)

- `action_skip()`
  - Marks current waypoint skipped (via selection change)
  - `_select_next_waypoint()`

- `action_back()`
  - Transitions `FLY_* -> CHART_REVIEW`
  - Switches phase to `chart`

- `action_forward()`
  - Validates `LAND_REVIEW` availability
  - `coordinator.transition(LAND_REVIEW)` + `_switch_to_land_screen()`

- Intervention flow
  - `_handle_intervention(...)` → `InterventionModal` → `_on_intervention_result(...)`

## Execution Flow

- `_execute_current_waypoint()`
  - Marks waypoint `IN_PROGRESS` + saves flight plan
  - Builds `WaypointExecutor` with callbacks and limits
  - `run_worker(self._run_executor())`

- `_run_executor()`
  - `WaypointExecutor.execute()` → returns `ExecutionResult`

- `on_worker_state_changed()`
  - Handles `InterventionNeededError` or other failures
  - Calls `_handle_execution_result(result)`

- `_handle_execution_result(result)`
  - SUCCESS
    - Mark COMPLETE + save
    - Commit via git (receipt validation)
    - Parent epic check
    - Select next waypoint
    - If all complete: transition `LAND_REVIEW`
  - INTERVENTION_NEEDED / MAX_ITERATIONS / FAILED
    - Mark FAILED
    - Transition `FLY_INTERVENTION`
  - CANCELLED
    - Transition `FLY_PAUSED`

## Cross-Cutting Services

- `JourneyCoordinator`
  - Transition validation and persistence
  - Waypoint selection and completion checks

- `WaypointExecutor`
  - Iterative execution loop
  - Calls progress callback with `ExecutionContext`

- `ExecutionLogReader` / `ExecutionLogWriter`
  - Audit trail for each waypoint

- `GitService` + `ReceiptValidator`
  - Receipt validation
  - Commit/tag integration

---

## Extraction Boundary (Target)

Introduce `ExecutionController` to own the flow currently distributed across `FlyScreen`:
- `start / pause / resume / skip / retry`
- State transitions
- Selection logic + execution sequencing
- Handling of `ExecutionResult`

`FlyScreen` should become a thin UI layer: inputs, rendering, and modal handling.
