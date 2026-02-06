# Waypoints Refactor Plan (2026-02-05)

**Goal**: Address the current philosophical shortcomings (simplicity drift in FLY, incomplete flight tests, missing decision records, residual TODOs) while strengthening domain boundaries, testability, and iteration discipline.

This plan follows the Waypoints philosophy: bikes not Rube Goldberg; explicit domain language; alternatives considered; staged implementation; tests first; artifacts and UX quality treated as first-class.

---

## 1) Problem Framing (Symptoms vs Root Causes)

### Symptoms
- `src/waypoints/tui/screens/fly.py` mixes UI, orchestration, execution, git, and process management.
- Flight tests described in `docs/testing-strategy.md` are not implemented (only `flight-tests/self-host/`).
- Architectural decisions are not documented in a durable, discoverable format (no ADRs).
- TODOs indicate incomplete reliability paths (rollback, project status, prompt summarization).

### Root Causes
- FLY phase lacks a dedicated orchestration boundary with a minimal interface.
- Testing strategy is documented but not operationalized into a repeatable pipeline.
- Decision making is visible in review docs but not captured as formal architectural records.
- Recovery and rollback are acknowledged but not embedded in execution flow.

---

## 2) Solution Space (Alternatives)

### A) Minimal Reorg (Low risk, lowest impact)
- Move a few helper methods out of `fly.py` and keep orchestration in screen.
- Add one flight test (L0) to prove the pipeline.
- Document a single ADR.

**Pros**: Fast, minimal change.
**Cons**: Doesn’t address cross-layer coupling or reliability; doesn’t scale.

### B) Domain-First Refactor (Recommended)
- Introduce a dedicated FLY orchestration service to separate UI from execution.
- Implement a flight test harness with L0–L2 coverage.
- Add a lightweight ADR system and document the most significant changes.

**Pros**: Aligns with “bicycles,” clarifies boundaries, testable in isolation.
**Cons**: Moderate effort; requires careful migrations.

### C) Full Protocol-Driven Execution (High impact, high risk)
- Redesign FLY as a strict protocol engine with structured reports and stateful iteration.
- Add schema validation for all JSONL artifacts.
- Build a full QA system for acceptance criteria.

**Pros**: Strong correctness guarantees.
**Cons**: Large refactor; not necessary to address current shortcomings.

**Chosen**: **B) Domain-First Refactor**. It fixes current issues while keeping scope tight and allowing iterative upgrades to protocol rigor later.

---

## 3) Design Principles and Ubiquitous Language

### Domain Language
- **Execution Session**: the lifecycle of executing a flight plan.
- **Execution Controller**: domain service that governs run/pause/resume, metrics, and waypoint transitions.
- **Execution Report**: structured record emitted per waypoint attempt.
- **Flight Test**: an input spec with expected artifacts and a validation script.
- **Checkpoint**: a recoverable state boundary with persisted artifacts.

### Boundaries
- **TUI Screen**: render state, bind keys, dispatch domain commands.
- **Orchestration Layer**: enforce business invariants and state transitions.
- **Execution Engine**: run a waypoint with a protocol, return a report.
- **Persistence Layer**: versioned artifacts, recovery, replay.

---

## 4) Implementation Plan (Phased)

### Phase 0 — Baseline and Discovery (1-2 sessions)
**Objective**: Confirm scope, inventory risk, and lock acceptance tests before refactor.

**Tasks**
- Map current FLY flow (screen → coordinator → executor → logs) into a call graph.
- List all FLY entry points, side effects, and persistence paths.
- Identify contract surfaces for extraction (inputs, outputs, invariants).

**Artifacts**
- `docs/analysis/fly-callgraph.md` (new)
- `docs/analysis/fly-invariants.md` (new)

**Acceptance Criteria**
- All current FLY interactions documented and traceable to code.

---

### Phase 1 — Extract Execution Controller (Core Refactor)
**Objective**: Remove orchestration logic from `fly.py` and centralize it in the domain layer.

**Design**
- Create `src/waypoints/orchestration/execution_controller.py`.
- Provide a narrow interface:
  - `start_execution()`
  - `pause_execution()`
  - `resume_execution()`
  - `execute_next()`
  - `handle_intervention()`
- The controller owns:
  - transitions between `FLY_READY`, `FLY_EXECUTING`, `FLY_PAUSED`, `FLY_INTERVENTION`, `LAND_REVIEW`
  - selection of next waypoint via coordinator
  - metrics aggregation per waypoint
  - persistence of execution reports/logs

**Tasks**
- Move execution state transitions and waypoint selection logic into controller.
- Keep UI-specific concerns in `fly.py` (rendering, key bindings, modal display).
- Introduce a `ExecutionReport` data model in `src/waypoints/fly/`.

**Tests (TDD)**
- Add `tests/test_execution_controller.py` with happy-path and failure-path tests.
- Ensure controller behavior is deterministic and easily mocked in TUI tests.

**Acceptance Criteria**
- `fly.py` no longer manages execution state transitions directly.
- `ExecutionController` is test-covered and used by TUI.
- All existing tests pass.

---

### Phase 2 — Flight Test Harness (BDD)
**Objective**: Operationalize the documented testing strategy with L0–L2 flight tests.

**Design**
- Create structure:
  - `flight-tests/L0-hello-world/`
  - `flight-tests/L1-todo-cli/`
  - `flight-tests/L2-rest-api/`
- For each flight test:
  - `input/idea.txt`
  - `expected/min_files.txt`
  - `expected/smoke_test.sh`
  - `results/<timestamp>/` (generated)

**Tasks**
- Add a small runner in `scripts/run_flight_test.py`.
- Document usage in `docs/testing-strategy.md`.

**Tests**
- Add `tests/test_flight_test_runner.py` to validate runner behavior.

**Acceptance Criteria**
- L0–L2 tests are runnable and repeatable locally.
- Results are stored in timestamped directories.

---

### Phase 3 — Decision Records (ADR system)
**Objective**: Capture architectural decisions in a durable, searchable format.

**Design**
- Add `docs/adr/README.md` (index).
- Create ADRs for:
  - FLY execution boundary extraction
  - Flight test harness
  - Execution report model

**Acceptance Criteria**
- ADR index linked from `docs/README.md` and `README.md`.

---

### Phase 4 — Reliability Polish (Targeted TODOs)
**Objective**: Resolve remaining TODOs that affect reliability and trust.

**Tasks**
- Implement rollback in coordinator when GitService supports it (or define explicit TODO with issue ID).
- Add `status` to `Project` model if still missing.
- Replace prompt prefix usage in `llm/prompts/fly.py` with proper spec summary.

**Acceptance Criteria**
- All TODOs in `rg "TODO"` for core runtime are resolved or turned into tracked issues.

---

## 5) Acceptance Criteria (Global)

- FLY orchestration is isolated in `ExecutionController` and test-covered.
- TUI screens are thin and focused on display and user interaction.
- L0–L2 flight tests can be executed with a single command.
- ADRs exist and are linked from doc indexes.
- No runtime TODOs remain untracked.

---

## 6) Testing Strategy Alignment

**Unit**
- `tests/test_execution_controller.py`
- `tests/test_flight_test_runner.py`

**Integration**
- FLY screen tests should mock the controller and verify UI flow only.

**BDD / Acceptance**
- L0–L2 flight tests with smoke tests and minimal expected files.

---

## 7) Migration & Compatibility

- Provide adapters in `fly.py` to minimize UI breakage during refactor.
- Keep existing log formats; add new `ExecutionReport` as additive data.
- If schema versioning is introduced, add migration in `models/schema.py`.

---

## 8) Work Breakdown (Issue-Oriented)

1. **Execution Controller extraction**
2. **Execution report model**
3. **TUI FLY screen integration**
4. **Flight test runner + L0**
5. **L1 and L2 flight tests**
6. **ADR system + first three ADRs**
7. **TODO reliability fixes**

---

## 9) Definition of Done

- New architecture reviewed, tests passing, and behavior preserved.
- All acceptance criteria satisfied.
- Docs updated and consistent with implementation.
- Flight tests operational and repeatable.

---

## 10) Ownership and Iteration

This plan is staged to keep every step testable and reviewable. Each phase is an MVP for the next: the execution controller enables better tests, the flight tests expose reliability gaps, and ADRs preserve context for future contributors.

If any step requires scope expansion, create a new ADR and update the plan rather than silently extending complexity.
