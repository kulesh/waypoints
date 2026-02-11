# Domain Model and Ubiquitous Language

This document is the canonical vocabulary for Waypoints runtime behavior.
Use it as the source of truth for naming in code, docs, prompts, tests, and
issue descriptions.

## Scope

This language covers runtime and persistence semantics for:

1. project and journey lifecycle
2. planning and waypoint execution
3. intervention, rollback, and verification
4. artifacts and evidence

## Bounded Contexts

1. `Project Management`:
   - project identity, journey state, phase transitions, persistence roots
2. `Planning (CHART)`:
   - flight plan, waypoint graph, dependencies, history
3. `Execution (FLY)`:
   - executor iterations, intervention handling, rollback/commit policy
4. `Verification`:
   - host validation evidence, acceptance-criterion verification, receipt verdict
5. `Iteration Intake (LAND iterate)`:
   - iteration request triage, drafted/linked waypoint lineage
6. `Agent Governance`:
   - development covenant, guidance projection, clarification question/response flow

## Core Domain Objects

1. `Project`:
   - aggregate root for one software journey
   - identity: `slug`
   - persistence anchor: `<projects-root>/<slug>/`
2. `Journey`:
   - finite-state lifecycle of a project (`spark:*` -> `land:review`)
   - state transition rules and recovery behavior
3. `FlightPlan`:
   - ordered graph of waypoints for implementation
4. `Waypoint`:
   - executable unit of work in FLY
   - contains objective, acceptance criteria, dependencies, status
5. `Intervention`:
   - structured pause requiring user/operator decision
6. `ChecklistReceipt`:
   - captured validation and criterion evidence used for trust gating
7. `IterationRequestRecord`:
   - LAND iterate request lifecycle from submission to waypoint linkage
8. `DevelopmentCovenant`:
   - canonical philosophy and engineering guideline set owned by orchestrator
9. `GuidancePacket`:
   - scoped projection of covenant delivered to a role turn
10. `ClarificationRequest`:
   - agent-raised question when policy or intent is ambiguous
11. `ClarificationResponse`:
   - orchestrator answer that resolves a clarification request

## Lifecycle Terms

1. `Journey State`:
   - exact runtime state (`fly:executing`, `chart:review`, etc.)
2. `Phase`:
   - UI routing abstraction (`fly`, `chart`, `shape`, etc.)
3. `Waypoint Status`:
   - `pending`, `in_progress`, `failed`, `skipped`, `complete`
4. `Completion`:
   - canonical meaning: orchestrator accepts outcome and waypoint status becomes
     `complete`

## Governance Terms

1. `Development Covenant`:
   - authoritative philosophy/guideline contract for agent behavior
   - default source: repository policy docs (for example `AGENTS.md`)
2. `Guidance Packet`:
   - turn-scoped guidance payload derived from covenant
   - must accompany each builder/verifier turn
3. `Doubt Escalation`:
   - required agent behavior when confidence is insufficient for safe decisions
   - mechanism: `ClarificationRequest` -> `ClarificationResponse`

## Verification Terms

Use these terms precisely:

1. `Validation`:
   - command-based checks (tests, lint, format, type-check) with captured stdout,
     stderr, and exit code
2. `Criterion Verification`:
   - evidence that a specific acceptance criterion is met/failed
3. `Receipt Verification`:
   - verdict over the receipt evidence payload (currently LLM judge + format checks)
4. `Builder Completion Claim`:
   - `<waypoint-complete>{waypoint_id}</waypoint-complete>` marker
   - this is a handoff signal, not terminal success

## Git Safety Terms

1. `Rollback Reference`:
   - canonical term for reset target (`HEAD`, tag, or commit-ish)
2. `Rollback Tag`:
   - legacy alias in some APIs/UI payloads
   - treat as compatibility naming, not preferred vocabulary
3. `Safe Anchor`:
   - a known-good commit reference used for recovery

## Persistence Terms

1. `Projects Root`:
   - base directory for project aggregates
   - resolved from `settings.project_directory`
2. `Project Root`:
   - `<projects-root>/<slug>/`
3. `Workspace Control Directory`:
   - `.waypoints/` under the launch workspace (default projects-root parent)
4. `Flight Plan File`:
   - canonical path: `flight-plan.jsonl`

## Protocol Terms

Execution protocol tags:

1. `<execution-stage>`: stage progress report
2. `<validation>`: command-level evidence marker
3. `<acceptance-criterion>`: criterion-level evidence marker
4. `<waypoint-complete>`: builder completion claim marker

Multi-agent protocol artifacts:

1. `GuidancePacket`: propagated policy context
2. `ClarificationRequest`: agent uncertainty handoff
3. `ClarificationResponse`: orchestrator resolution handoff

## Canonical vs Legacy Aliases

1. Canonical: `rollback reference`
   - Legacy aliases: `rollback tag`, `last_safe_tag`
2. Canonical: `builder completion claim`
   - Legacy interpretation: `waypoint-complete` as terminal success
3. Canonical: `flight-plan.jsonl`
   - Legacy mention: `flight-plan.json` in some docs
4. Canonical: `epic waypoint` (parent waypoint with children)
   - Legacy alias: `multi-hop waypoint`
5. Canonical: `development covenant`
   - Legacy/ambiguous aliases: `agent instructions`, `system prompt rules`
6. Canonical: `clarification request`
   - Legacy/ambiguous aliases: `question`, `ask orchestrator`

## Naming Rules

1. Prefer domain nouns from this document for new APIs.
2. Keep legacy field names only for compatibility boundaries.
3. If legacy naming is preserved, annotate with a compatibility note in code/doc.
4. Do not introduce synonyms for `Project`, `Journey`, `FlightPlan`, `Waypoint`,
   `Intervention`, or `ChecklistReceipt`.
5. Prefer `development covenant` + `guidance packet` over generic `instructions`
   in multi-agent protocol design docs.

## Drift Findings (2026-02-11 audit)

1. `README.md` persistence example used `flight-plan.json` and implied project data
   lived directly in `.waypoints/`; runtime persists per-project under
   `<projects-root>/<slug>/` and uses `flight-plan.jsonl`.
2. Rollback vocabulary is mixed in compatibility surfaces (`rollback_tag`,
   `rollback_to_tag`) while canonical runtime naming is reference-based
   (`rollback_ref`, `rollback_to_ref`).
3. Verification language mixes `validation`, `verification`, and `completion`
   informally; this document now defines strict semantics for each term.
