# Waypoints Design and Architecture Assessment

## Executive Summary
Waypoints has a coherent, phase-driven product vision and a clear, modular
implementation. The current architecture cleanly separates the TUI, domain
models, persistence, LLM interaction, execution, and git/receipt validation.
The biggest risks are reliability and determinism in the FLY phase, resilience
of persistence formats, and the lack of explicit state-machine boundaries.
If addressed, Waypoints has a strong foundation to become a durable "idea to
software" environment.

## Strengths
- Clear journey model (SPARK/SHAPE/CHART/FLY) reflected consistently in code.
- Strong separation of concerns: UI, models, persistence, LLM client, executor,
  and git services are cleanly split.
- Append-friendly JSONL logs for dialogue, flight plans, and execution provide
  crash tolerance and auditability.
- "Pilot and dog" receipt validation gives a pragmatic trust-but-verify layer.
- TUI is consistent, keyboard-first, and uses custom messages to reduce tight
  coupling between widgets and screens.

## Weaknesses and Risks
- State is implicit across screens; no explicit state machine or persistence
  contract defines valid transitions and recovery behavior.
- JSONL parsing is lenient but has no schema versioning or migration strategy.
- LLM output parsing (e.g., waypoint JSON) is fragile and unvalidated.
- FLY executor relies on a single model output marker; no structured protocol
  to guarantee completion quality.
- Limited observability: execution logs are available, but no systematic
  telemetry or metrics aggregation.
- Global config vs per-project config is inconsistent (some in .waypoints/,
  some in home config, some in project directories).

## Architecture Improvements (Actionable)
1) Introduce an explicit state machine for the journey.
   - Add a `JourneyState` enum and a `StateTransition` table.
   - Persist current state in `.waypoints/projects/<slug>/project.json`.
   - Validate transitions on app startup and before screen switches.

2) Add schema versioning and validation for JSONL artifacts.
   - Prefix each JSONL header with `schema_version`.
   - Validate records with `pydantic` or `attrs` validators.
   - Provide a migration step on load for older versions.

3) Harden LLM outputs with structured parsing.
   - Require JSON schema for waypoints and sub-waypoints.
   - Validate IDs, parent/child linkage, and dependency cycles.
   - If parsing fails, prompt the model to repair output.

4) Make the FLY executor protocol explicit.
   - Use a structured control channel: e.g., "PLAN", "TESTS", "CODE", "RUN".
   - Require a structured "execution report" JSON at end of each iteration.
   - Gate completion on validated report + receipt.

5) Centralize persistence paths and config.
   - Add a `Paths` or `Storage` module that resolves workspace vs user config.
   - Use one place to handle `.waypoints/` paths for all components.

6) Improve observability and metrics.
   - Aggregate per-waypoint: time, iterations, cost, pass/fail.
   - Add a `metrics.jsonl` summary per project for trend analysis.
   - Show a light-weight report in FLY and CHART screens.

7) Add safety and rollback controls.
   - Pre-flight check that ensures clean git state or asks permission.
   - Allow rollback to last "safe" tag if FLY execution goes wrong.

## Product Utility and Future Assessment
Utility: High potential. The value proposition (turn ideas into plans and
autopilot execution) is strong for solo builders and small teams. The TUI
keeps friction low and aligns with developer workflows.

Future outlook: Promising, but viability depends on reliability in the FLY
phase, correctness guarantees, and trust signals. If Waypoints can reliably
produce high-quality artifacts with predictable costs, it can become a daily
driver for greenfield projects.

Key success dependencies:
- Deterministic execution flow and measurable reliability.
- Clear, user-visible checkpoints and rollback options.
- Excellent artifact quality (brief/spec/plan) to earn trust early.

## Suggested Feature Roadmap Improvements
Short-term (MVP+):
- Project resume with explicit "resume checkpoints".
- JSON schema validation and migration tooling.
- Robust "Regenerate" flows for brief/spec/waypoints with diffs.
- Execution telemetry summary view.

Mid-term:
- Waypoint execution sandboxing and dependency-aware scheduling.
- "Explain my plan" and "Why this waypoint?" transparency features.
- Local cache of model outputs for reproducibility.

Long-term:
- Multi-project portfolio management.
- Collaborative review mode with shareable artifacts.
- Reverse-engineering existing repos into waypoints (post-MVP goal).

## Journey State Machine (Proposed)
```
SPARK
  |
  v
SHAPE (Q&A)
  |
  v
IDEA BRIEF
  |
  v
PRODUCT SPEC
  |
  v
CHART (Flight Plan)
  |
  v
FLY (Execution) ---> [INTERVENTION] ---> (Resume or Rollback)
  |
  v
LAND (All Waypoints Complete)
```

Notes:
- Each transition should validate state and persist a checkpoint.
- FLY should allow pause/resume and rollback to last safe tag.
- INTERVENTION should create a recovery path (fix, retry, or skip).

## Scoring Rubric (Current vs Target)

| Dimension | Current (1-5) | Target (1-5) | Notes |
|---|---|---|---|
| Reliability (FLY) | 2 | 5 | Needs deterministic checks + receipt gating |
| Output Quality (Brief/Spec) | 3 | 5 | Add quality gates and guided edits |
| UX Clarity | 4 | 5 | Strong TUI, add explicit state checkpoints |
| Cost Predictability | 2 | 4 | Add telemetry + budget caps |
| Safety / Rollback | 2 | 5 | Require rollback and pre-flight checks |
| Observability | 2 | 4 | Add metrics aggregation and summary view |
| Reproducibility | 2 | 4 | Cache model outputs and standardize env |

## 100X Improvements (High-Impact Features)
1) Trust and Reliability Layer
   - Deterministic execution checks: run tests, lint, type-check in a standard
     pipeline with a verified report.
   - Receipt includes test outputs and git diff summaries.
   - Automatic rollback on failed criteria.

2) Execution Intelligence
   - Auto-split waypoints based on runtime complexity and test coverage.
   - Dynamic reprioritization based on dependency graph and risk.

3) "Journey Replay" and Auditability
   - Timeline view of decisions and artifacts.
   - Ability to replay or fork a journey from any waypoint.

4) Collaborative Pilot Mode
   - Pairing mode where AI suggests and human approves before execution.
   - Structured review steps at major transitions.

5) Live Artifact Quality Gates
   - Automated rubric scoring for briefs/specs.
   - Inline suggestions to improve clarity or feasibility before CHART.

6) Full-stack Execution Environment
   - Virtual project runner with sandboxed dependencies and reproducible builds.
   - Standardized environment template per language/framework.

## Concrete Next Steps (Engineering)
1) Add `JourneyState` and validation in `Project` and `WaypointsApp`.
2) Introduce JSON schema validation for flight plans and receipts.
3) Add a parsing repair step for waypoint generation output.
4) Implement a metrics aggregator for execution logs.
5) Add "resume checkpoint" to session headers and UI.

## Final Assessment
Waypoints has a strong conceptual backbone and a clean initial architecture.
The main gaps are reliability, explicit state control, and structured output
verification. Addressing these will elevate the product from promising to
trusted, and create the foundation for the 100X vision.
