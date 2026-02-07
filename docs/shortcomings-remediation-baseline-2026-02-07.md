# Shortcomings Remediation Baseline (2026-02-07)

This baseline supports:
- `waypoints-30s` (Phase 0)
- `waypoints-6ob` (program epic)

## Execution Environment

- Branch: `codex/shortcomings-implementation`
- Worktree: `/Users/kulesh/dev/waypoints-shortcomings`
- Source branch for worktree: `main` @ `3b37dfd` (2026-02-06)

## Quality Gate Baseline

Run on 2026-02-07 in this worktree:

- `uv run pytest`: `632 passed`
- `uv run ruff check .`: pass
- `uv run ruff format --check .`: pass
- `uv run mypy src/`: pass
- Coverage summary: `47%` total

## Size and Complexity Snapshot

- Python source files: `110`
- Test files: `40`
- Docs markdown files: `18`
- Source LOC: `33,029`
- Test LOC: `11,314`

Largest Python modules:
- `src/waypoints/tui/screens/fly.py` (`2612` lines)
- `src/waypoints/tui/widgets/flight_plan.py` (`1468` lines)
- `src/waypoints/fly/executor.py` (`1366` lines)

## Key Gaps Captured

1. UI/business boundary still too thick in Fly screen
- Concentrated logic remains in `src/waypoints/tui/screens/fly.py`.
- Remediation phase: `waypoints-7h4`.

2. Duplicate fly-domain logic surface
- `src/waypoints/orchestration/coordinator_fly.py` is test-covered but not runtime-integrated.
- Remediation phase: `waypoints-0pn`.

3. BDD/flight-test docs reference missing executables
- `docs/testing-strategy.md` references `scripts/run_flight_tests.py` (missing).
- `flight-tests/self-host/README.md` references `run.sh` and `report.py` (missing).
- Remediation phase: `waypoints-08q`.

4. Strategic modules lack effective confidence coverage
- `src/waypoints/verify/orchestrator.py` coverage is `0%`.
- `src/waypoints/verify/compare.py` coverage is `0%`.
- `src/waypoints/verify/models.py` coverage is `0%`.
- `src/waypoints/runners/run_*.py` coverage is `0%`.
- Remediation phase: `waypoints-bl0`.

5. Documentation/index drift
- `AGENTS.md` references `tests/test_waypoints.py::test_hello_default` (missing test).
- Architecture/history docs still include legacy `LANDED` examples.
- Remediation phase: `waypoints-w8w`.

6. Known TODOs in active flow
- `src/waypoints/orchestration/fly_phase.py`: rollback TODO.
- `src/waypoints/tui/screens/land.py`: project status TODO.
- Tracked under downstream refactor phases (`waypoints-7h4`, `waypoints-0pn`).

7. Worktree-specific `bd` hygiene warnings
- Fresh-clone DB bootstrap warnings in this worktree.
- `.beads/.gitignore` drift warning still present.
- Remediation phase: `waypoints-77j`.

## Ordered Implementation Sequence

1. `waypoints-30s` Phase 0 (baseline + acceptance metrics)
2. `waypoints-7h4` Phase 1 (FlyScreen boundary refactor)
3. `waypoints-0pn` Phase 2 (remove/integrate `coordinator_fly`)
4. `waypoints-08q` Phase 3 (executable flight-test workflow)
5. `waypoints-bl0` Phase 4 (verify/runners confidence)
6. `waypoints-w8w` Phase 5 (docs/index drift)
7. `waypoints-77j` Phase 6 (`bd` hygiene)

## Phase 0 Exit Criteria

- Baseline metrics recorded in-repo.
- Program backlog exists in `bd` with dependency order.
- Phase 1 issue is unblocked and ready to execute.
