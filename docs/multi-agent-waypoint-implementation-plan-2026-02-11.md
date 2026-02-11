# Comprehensive Implementation Plan: ADR-0004 Multi-Agent FLY (2026-02-11)

## Purpose

Implement `docs/adr/0004-multi-agent-fly-handoff-protocol.md` end-to-end with:

1. explicit multi-agent handoff contracts
2. orchestrator-owned development governance (`DevelopmentCovenant`)
3. mandatory doubt escalation (`ClarificationRequest` / `ClarificationResponse`)
4. reduced token waste through bounded context/tool-output policy
5. backward-compatible integration with existing receipt and waypoint lifecycle flow

## Scope

In scope:

1. control-plane protocol models and artifact persistence
2. builder/verifier role separation with permission enforcement
3. orchestrator decision engine for accept/rework/rollback/escalate
4. guidance packet propagation and clarification loop
5. context envelope budgeting + exploration-first retrieval
6. skills attachment model
7. rollout, migration, observability, and benchmarking

Out of scope (explicitly deferred):

1. concurrent execution of multiple waypoints
2. external orchestrator service split
3. cross-repo multi-project scheduling

## Success Criteria (Release Gates)

Functional correctness:

1. every role turn emits typed, versioned artifacts
2. only orchestrator can mutate waypoint status to `complete`
3. verifier write/edit attempts are rejected by policy
4. unresolved clarification never auto-completes a waypoint

Quality/economics:

1. first-pass acceptance rate is non-regressive vs single-agent baseline
2. median tokens per waypoint reduced by at least 20% in benchmark corpus
3. rollback/intervention rate is non-regressive
4. p95 end-to-end waypoint latency increase stays within 25%

Compatibility:

1. existing `<execution-stage>`, `<validation>`, `<acceptance-criterion>`,
   `<waypoint-complete>` parsing remains valid
2. receipt pipeline (`ChecklistReceipt`) remains canonical evidence contract
3. single-agent mode remains available behind config flag

## Baseline Before Implementation

Establish baseline from current single-agent flow before enabling new gates:

1. capture 30-waypoint benchmark corpus (mixed bugfix/feature/refactor)
2. record baseline metrics:
   - first-pass acceptance
   - retries per waypoint
   - rollback/intervention rate
   - total/median tokens
   - p50/p95 latency
3. pin baseline dataset and command runner config in-repo for reproducibility

## Workstreams

### WS1: Protocol Contracts and Persistence

Deliverables:

1. protocol models for:
   - `GuidancePacket`
   - `BuildPlan`
   - `BuildArtifact`
   - `VerificationRequest`
   - `VerificationReport`
   - `ClarificationRequest`
   - `ClarificationResponse`
   - `OrchestratorDecision`
2. artifact serialization/deserialization + schema versioning
3. execution-log persistence for all control-plane artifacts

Primary files:

1. `src/waypoints/fly/protocol.py`
2. `src/waypoints/fly/execution_log.py`
3. `src/waypoints/fly/types.py`

Acceptance:

1. all artifacts include required metadata (`schema_version`, `artifact_id`,
   `waypoint_id`, `produced_by_role`, `produced_at`, `source_refs`)
2. schema migration tests cover additive fields and unknown-field tolerance

### WS2: Orchestrator Decision Engine

Deliverables:

1. deterministic decision table for `accept`, `rework`, `rollback`, `escalate`
2. reason codes for all orchestrator decisions
3. retry/clarification budget accounting and exhaustion handling

Primary files:

1. `src/waypoints/orchestration/fly_phase.py`
2. `src/waypoints/orchestration/coordinator.py`
3. `src/waypoints/orchestration/coordinator_fly.py`

Acceptance:

1. decision transitions are fully unit-tested
2. every disposition references triggering artifact ids
3. status mutation remains coordinator/orchestrator-only

### WS3: Role Runtime Separation and Permission Policy

Deliverables:

1. builder runner profile (read/write/edit + bounded command execution)
2. verifier runner profile (read/search/test only; no write/edit)
3. optional repair runner behind feature flag

Primary files:

1. `src/waypoints/fly/executor.py`
2. `src/waypoints/llm/tools.py`
3. `src/waypoints/orchestration/headless_fly.py`

Acceptance:

1. verifier write/edit operations fail fast with policy error
2. builder/verifier prompts are role-scoped and contract-driven
3. fallback to single-agent mode remains operational

### WS4: Governance Propagation (`DevelopmentCovenant`)

Deliverables:

1. covenant loader (default source: `AGENTS.md`, with deterministic snapshot)
2. `GuidancePacket` injection into every builder/verifier turn
3. guidance provenance logging (`covenant_version`, `policy_hash`)

Primary files:

1. `src/waypoints/orchestration/fly_phase.py`
2. `src/waypoints/llm/prompts/fly.py`
3. `src/waypoints/fly/execution_log.py`

Acceptance:

1. no role turn executes without guidance packet
2. provenance is visible in execution logs and debugging output
3. replay of a historical run can reconstruct policy context exactly

### WS5: Clarification Loop and Doubt Escalation

Deliverables:

1. role-triggered `ClarificationRequest` emission for low-confidence policy calls
2. orchestrator `ClarificationResponse` path with updated constraints
3. bounded clarification rounds, then escalation/intervention

Primary files:

1. `src/waypoints/fly/protocol.py`
2. `src/waypoints/orchestration/fly_phase.py`
3. `src/waypoints/fly/intervention.py`
4. `src/waypoints/tui/screens/intervention.py`

Acceptance:

1. uncertainty scenarios produce clarification artifacts before risky actions
2. unresolved clarification cannot pass to `accept`
3. intervention includes actionable operator options when clarification stalls

### WS6: Context Envelope and Token Governance

Deliverables:

1. `ContextEnvelope` with per-role prompt/tool-output budgets
2. exploration-first retrieval sequence:
   - repo map/index
   - targeted search/symbol lookup
   - bounded file reads
3. tool-output clipping + structured summaries before context reinjection

Primary files:

1. `src/waypoints/memory/project_index.py`
2. `src/waypoints/memory/waypoint_memory.py`
3. `src/waypoints/fly/executor.py`
4. `src/waypoints/llm/metrics.py`

Acceptance:

1. envelope overflow behavior is deterministic (truncate with metadata or fail-fast)
2. source attribution is preserved for all included context slices
3. benchmark token target is met without pass-rate regression

### WS7: Skills Framework

Deliverables:

1. versioned skill spec under `docs/skills/`
2. resolver that attaches relevant skills by stack/profile signals
3. initial packs:
   - `python-pytest-ruff`
   - `typescript-node`
   - `rust-cargo`

Primary files:

1. `docs/skills/*`
2. `src/waypoints/orchestration/fly_phase.py`
3. `src/waypoints/llm/prompts/fly.py`

Acceptance:

1. only relevant skills are attached
2. skill selection and usage are logged per waypoint
3. stale/invalid skill schema fails validation pre-run

### WS8: Rollout, Migration, and Operations

Deliverables:

1. feature flags:
   - `fly.multi_agent.enabled`
   - `fly.multi_agent.verifier_enabled`
   - `fly.multi_agent.repair_enabled`
   - `fly.multi_agent.clarification_required`
2. staged rollout:
   - shadow verifier
   - advisory verifier
   - required verifier gate
3. troubleshooting and rollback playbook

Primary files:

1. `src/waypoints/config/settings.py`
2. `README.md`
3. `docs/testing-strategy.md`

Acceptance:

1. one-command rollback to single-agent mode
2. no data-loss migration
3. operations docs cover failure signatures and recovery steps

## Implementation Waves (Execution Order)

### Wave A: Contracts and Decision Core

Includes:

1. WS1
2. WS2
3. skeleton of WS3

Exit criteria:

1. end-to-end builder->verifier->decision flow works in tests
2. no behavioral regression in existing FLY path

### Wave B: Governance and Clarification

Includes:

1. WS4
2. WS5

Exit criteria:

1. guidance packet enforced for every role turn
2. low-confidence scenarios route through clarification artifacts

### Wave C: Context Efficiency and Skills

Includes:

1. WS6
2. WS7

Exit criteria:

1. token reduction target is met on benchmark corpus
2. skill resolver behaves deterministically and is test-covered

### Wave D: Rollout and Hardening

Includes:

1. WS8
2. CI benchmark + scenario tests
3. release checklist

Exit criteria:

1. multi-agent mode is default-ready behind reversible flags
2. operational docs + runbooks are complete

## Backlog Split (Beads)

1. `MA-01` protocol artifact models + schema versioning
2. `MA-02` artifact persistence in execution log
3. `MA-03` orchestrator decision table + reason codes
4. `MA-04` retry/clarification budget accounting
5. `MA-05` builder/verifier runner split + permission enforcement
6. `MA-06` single-agent fallback compatibility gates
7. `MA-07` covenant loader + versioned snapshot
8. `MA-08` guidance packet injection + provenance logging
9. `MA-09` clarification request/response orchestration path
10. `MA-10` clarification exhaustion -> intervention policy
11. `MA-11` context envelope model + overflow behavior
12. `MA-12` exploration-first retrieval helpers
13. `MA-13` tool-output clipping/summarization reinjection policy
14. `MA-14` per-role metrics (tokens, latency, retries, decisions)
15. `MA-15` skill schema + resolver
16. `MA-16` initial skill packs (python/typescript/rust)
17. `MA-17` rollout flags + shadow/advisory/required verifier modes
18. `MA-18` migration docs, runbooks, and release checklist

Dependency order:

1. `MA-01` -> `MA-02` -> `MA-03`
2. `MA-03` -> `MA-04` -> `MA-09` -> `MA-10`
3. `MA-05` -> `MA-06`
4. `MA-07` -> `MA-08` -> `MA-09`
5. `MA-11` -> `MA-12` -> `MA-13`
6. `MA-14` runs in parallel once `MA-02` exists
7. `MA-15` -> `MA-16`
8. `MA-17` depends on `MA-03`, `MA-05`, `MA-08`, `MA-09`
9. `MA-18` last, after `MA-17`

## Test Strategy and Gates

Unit tests:

1. artifact schema validation
2. decision table transitions
3. permission policy enforcement
4. clarification routing and exhaustion behavior
5. context envelope overflow/truncation behavior

Integration tests:

1. builder->receipt->verifier->decision happy path
2. verifier failure -> rework loop
3. rollback path with rollback reference resolution
4. unresolved clarification -> intervention
5. single-agent fallback path

Benchmark tests:

1. fixed corpus run in baseline and multi-agent mode
2. compare pass rate, tokens, latency, retries, rollback/intervention
3. require gate thresholds from Success Criteria

Release quality gates:

1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run mypy src/`
4. `uv run pytest`
5. benchmark gate script (new) must pass

## Operational Runbook Requirements

Must document:

1. how to inspect guidance packet provenance for a waypoint run
2. how to inspect clarification artifacts and decision reasons
3. how to force fallback to single-agent mode
4. how to recover from rollback/escalation outcomes
5. how to tune budget thresholds safely

## Risks and Mitigations

1. extra turns increase latency
   - Mitigation: strict turn caps, clarification caps, shadow-mode calibration
2. protocol complexity increases failure modes
   - Mitigation: typed artifacts + reason-coded deterministic decisions
3. governance drift between docs and runtime prompts
   - Mitigation: covenant snapshot hash + fail-fast missing-guidance checks
4. token clipping removes critical evidence
   - Mitigation: truncation metadata + verifier low-confidence escalation
5. skill packs become stale
   - Mitigation: schema versioning + periodic benchmark validation

## Completion Definition

The implementation is complete when:

1. all backlog items `MA-01` through `MA-18` are closed
2. all release quality gates and benchmark thresholds pass
3. ADR invariants are enforced by tests
4. rollback to single-agent mode is proven in staging tests
5. documentation and runbooks are updated and linked from `docs/README.md`
