# ADR-0004: Multi-Agent Fly Handoff Protocol

## Context

FLY execution currently behaves like a single compound agent loop:

- one builder-style agent performs implementation, testing, and self-verification
- orchestration decisions are mostly iteration/error/rollback control around that loop
- tool calls and tool outputs accumulate in-session, which dominates token spend

Evidence from `docs/fly-token-usage-analysis-2026-02-06.md` shows cost variance is
driven primarily by dynamic tool-loop behavior (tool calls and large tool outputs),
not by static prompt variance.

Current strengths:

- clear orchestration ownership (`JourneyCoordinator` / `FlyPhase`)
- robust intervention and rollback pathways
- project memory index and waypoint memory for baseline context guidance

Current gaps for multi-agent reliability and token efficiency:

1. no explicit role separation between builder and independent verifier
2. no formal handoff artifacts between execution stages
3. no explicit context budgets per role/turn
4. limited exploration-first retrieval: tools are generic (`read`, `glob`, `grep`,
   `bash`) without first-class "plan context before loading large files"
5. no project-level skill model for reusable, stack-specific execution recipes
6. no explicit shared development philosophy/guideline contract across roles
7. no formal doubt-escalation loop for agent clarification before high-impact
   decisions

## Decision

Adopt a multi-agent FLY architecture based on explicit handoff protocols.

Core roles:

1. `OrchestratorAgent` (control plane):
   - owns waypoint lifecycle and stop conditions
   - routes work to other agents
   - decides accept/rework/rollback/escalate
   - owns canonical development philosophy and implementation guidelines
2. `BuilderAgent` (write plane):
   - implements waypoint changes
   - can read/write/edit and run bounded commands
3. `VerifierAgent` (judge plane):
   - validates acceptance criteria with independent evidence
   - read/test only; no write/edit permissions
4. optional `RepairAgent`:
   - targeted fix loop when verifier reports failures

Handoff protocol artifacts (versioned JSON):

1. `GuidancePacket`
2. `BuildPlan`
3. `BuildArtifact`
4. `VerificationRequest`
5. `VerificationReport`
6. `ClarificationRequest`
7. `ClarificationResponse`
8. `OrchestratorDecision`

Artifact minimum contract:

1. all artifacts must include:
   - `schema_version`
   - `artifact_id`
   - `waypoint_id`
   - `produced_by_role`
   - `produced_at`
   - `source_refs`
2. `GuidancePacket` must include:
   - `covenant_version`
   - `policy_hash`
   - role-specific constraints and stop conditions
3. `BuildPlan` must include:
   - intended file targets
   - validation plan
   - acceptance-criterion coverage map
   - budget envelope
4. `BuildArtifact` must include:
   - actual touched files and diff summary
   - command ledger (command, exit code, evidence ref)
   - criterion coverage claims
   - builder completion claim marker payload
5. `VerificationRequest` must include:
   - criteria under review
   - expected evidence set
   - policy constraints applied to verifier
6. `VerificationReport` must include:
   - per-criterion verdict (`pass` | `fail` | `inconclusive`)
   - evidence references
   - unresolved doubts, if any
7. `ClarificationRequest` must include:
   - blocking question
   - decision context
   - confidence level and requested options
8. `ClarificationResponse` must include:
   - chosen option/answer
   - rationale
   - updated constraints if any
9. `OrchestratorDecision` must include:
   - disposition (`accept` | `rework` | `rollback` | `escalate`)
   - reason code
   - referenced artifact ids
   - status mutation (if applied)

Planning ownership:

1. no shared planning agent in the initial design
2. builder creates `BuildPlan` from waypoint/spec constraints
3. verifier creates an independent verification plan from criteria/evidence needs
4. orchestrator only provides immutable execution contract (scope, budgets, stops)

Project management context ownership:

1. orchestrator owns canonical `ProjectManagementContext` for waypoint lifecycle:
   - completion graph
   - dependency/blocking state
   - retries/interventions/rollbacks
   - receipt outcomes and budget telemetry
2. builder and verifier receive scoped read-only `ContextEnvelope` views
3. context sharing defaults to minimal push + explicit pull for additional slices
4. verifier context must not require builder narrative for initial judgment

Development guidance ownership:

1. orchestrator owns a canonical `DevelopmentCovenant`:
   - development philosophy
   - engineering guidelines
   - role-specific constraints and quality bars
2. default covenant source is repository guidance (for example `AGENTS.md`)
3. orchestrator snapshots covenant to a versioned payload (`covenant_version`,
   `policy_hash`) for deterministic replay
4. orchestrator attaches a scoped `GuidancePacket` to every builder/verifier turn
5. guidance propagation is mandatory; role prompts must not omit it

Clarification protocol:

1. builder/verifier must emit `ClarificationRequest` when they have unresolved
   doubt about:
   - interpretation of philosophy/guidelines
   - accept/reject threshold for evidence
   - conflicting constraints or ambiguous waypoint intent
2. orchestrator responds with `ClarificationResponse` and updated direction
3. clarification artifacts are persisted and referenced by subsequent handoff
   artifacts
4. if clarification cannot be resolved automatically, orchestrator escalates to
   human intervention with explicit options
5. clarification loop is bounded by policy (max clarification rounds per waypoint);
   exceeding limit must transition to intervention/escalation

Execution turn lifecycle:

1. orchestrator emits `GuidancePacket` + `ContextEnvelope` to the active role
2. role may emit `ClarificationRequest` before high-impact action
3. builder emits `BuildPlan`, executes, and emits `BuildArtifact`
4. builder completion claim (`<waypoint-complete>`) triggers receipt pipeline
5. host validation + receipt generation runs as current canonical path
6. verifier consumes `VerificationRequest` and emits `VerificationReport`
7. orchestrator emits `OrchestratorDecision` and applies status transition

Decision policy:

1. `accept`:
   - all criteria verified `pass`
   - no unresolved clarification
   - no policy violations
2. `rework`:
   - fixable verification failures or insufficient evidence
   - bounded retry budget remains
3. `rollback`:
   - safety/regression condition detected and rollback reference available
4. `escalate`:
   - unresolved clarification
   - policy conflict
   - retry/clarification budget exhausted

Context policy:

1. exploration-first context loading (index/search/symbol summary before raw file load)
2. per-role prompt and tool-output budgets with hard truncation/overflow summaries
3. canonical source precedence (`docs/product-spec.md` over stale waypoint summary)
4. evidence-first verifier prompts (criterion-by-criterion proof requirements)

Skills model:

1. introduce repository-managed skill packs under `docs/skills/`
2. each skill defines:
   - applicability (stack/signal matcher)
   - preferred commands
   - test strategy
   - anti-patterns and failure recovery
3. orchestrator attaches only relevant skills to builder/verifier context

Implementation strategy:

- Phase 1 ships "virtual multi-agent" execution in-process (sequential role turns)
- keep single-waypoint semantics and existing rollback model
- defer true concurrent multi-waypoint execution to a later ADR

## Protocol Compatibility

Multi-agent flow is additive and remains backward-compatible with current FLY
protocol primitives:

1. Builder continues to emit existing structured markers:
   - `<execution-stage>`
   - `<validation>`
   - `<acceptance-criterion>`
   - `<waypoint-complete>{waypoint_id}</waypoint-complete>`
2. `<waypoint-complete>` semantics change from "terminal success" to
   "builder handoff ready".
3. Receipt pipeline remains canonical:
   - host validation command execution
   - `ChecklistReceipt` generation with captured evidence
   - verifier verdict over receipt/evidence
4. Orchestrator applies final decision gate:
   - only orchestrator marks waypoint completed
   - reject path returns to rework/repair/rollback/escalation

Compatibility contract:

1. existing tag parsers and receipt schema stay valid
2. new fields are additive (for example `BuildArtifact` and
   `VerificationReport` attachments)
3. clarification exchanges are additive metadata and do not break current receipt
   flow
4. single-agent mode remains available as fallback behind feature flags
5. legacy marker/tag parsing remains valid; orchestrator-level artifacts are
   additional control-plane records

## Alternatives Considered

- Keep single-agent loop and tune prompt text
  - lower implementation effort, but does not create independent verification or
    formal handoff contracts.
- Parallel builders per waypoint now
  - premature without dependency-safe isolation and deterministic merge strategy.
- External orchestrator service first
  - adds infrastructure complexity before core protocol contracts are stable.

## Consequences

Positive:

1. explicit role boundaries reduce "self-grade" failure modes
2. handoff artifacts are auditable and testable
3. token controls become enforceable per agent role
4. skills provide reusable stack-specific guidance with lower prompt entropy

Tradeoffs:

1. more orchestration complexity and state transitions
2. additional model turns per waypoint (must be offset by fewer rework loops)
3. schema/version migration responsibilities for handoff artifacts
4. orchestrator prompt/context assembly grows due to mandatory guidance payloads

## Invariants

1. orchestrator remains the single authority for waypoint status mutation
2. verifier cannot modify workspace state
3. every acceptance criterion must map to explicit evidence in `VerificationReport`
4. rollback decisions require actionable remediation guidance when rollback cannot
   be applied automatically
5. builder completion marker alone cannot finalize waypoint success
6. orchestrator must attach `GuidancePacket` to every role turn
7. builder/verifier must raise `ClarificationRequest` instead of silently making
   low-confidence policy decisions
8. every orchestrator disposition must include reason code and artifact references
9. unresolved clarification cannot transition waypoint status to `complete`
