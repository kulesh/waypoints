# Testing Strategy for Waypoints

## Problem Statement

Waypoints is an AI-native software development system. Testing it requires validating not just code correctness, but **LLM effectiveness, UX quality, and output functionality**. Traditional pytest alone is insufficient.

### Testing Surface Area

| # | Layer | What to Test | Challenge |
|---|-------|-------------|-----------|
| 1 | LLM Prompts | Can build hello world → Waypoints itself | Non-deterministic, needs diverse benchmarks |
| 2 | UX Quality | Q&A effectiveness, document quality | Subjective, needs human eval or LLM-as-judge |
| 3 | Charting | Waypoint granularity, editing mechanisms | Both functional + quality judgment |
| 4 | Execution | Consistent waypoint implementation | Reliability over many runs |
| 5 | Artifacts | Audit trail, receipts, logs complete | Completeness verification |
| 6 | Output | Does the generated product work? | Requires running the built product |

---

## Testing Framework: Three Pillars

```
┌─────────────────────────────────────────────────────────────────┐
│                    PILLAR 1: BENCHMARK SUITE                     │
│   Reference projects that Waypoints builds (hello world → self)  │
├─────────────────────────────────────────────────────────────────┤
│                    PILLAR 2: QUALITY GATES                       │
│   LLM-as-judge + human evaluation for subjective quality         │
├─────────────────────────────────────────────────────────────────┤
│                    PILLAR 3: OUTPUT VALIDATION                   │
│   Smoke test → acceptance criteria → human review                │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pillar 1: Benchmark Suite

A set of reference projects with increasing complexity that Waypoints should be able to build.

### Proposed Benchmarks

| Level | Project | Tests |
|-------|---------|-------|
| L0 | Hello World CLI | Simplest possible: single file, prints output |
| L1 | Todo App (CLI) | CRUD, file persistence, basic state |
| L2 | REST API Server | HTTP endpoints, JSON, validation |
| L3 | TUI Application | Textual-based, interactive, multiple screens |
| L4 | LLM Integration | API calls, streaming, prompt management |
| L5 | Waypoints (self) | **The ultimate test**: can it build itself? |

### Benchmark Execution

```
benchmarks/
├── L0-hello-world/
│   ├── input/
│   │   └── idea.txt              # "A CLI that prints hello world"
│   ├── expected/
│   │   ├── min_files.txt         # Minimum expected files
│   │   └── smoke_test.sh         # ./hello should print "Hello"
│   └── results/                  # Generated on each run
│       └── 2026-01-08-run1/
├── L1-todo-cli/
│   ├── input/
│   │   └── idea.txt
│   ├── expected/
│   │   ├── min_files.txt
│   │   ├── acceptance_criteria.yaml
│   │   └── smoke_test.sh
│   └── results/
...
```

### Benchmark Metrics

For each benchmark run, capture:
- **Completion rate**: Did all waypoints complete?
- **Iteration count**: How many executor iterations per waypoint?
- **Total cost**: LLM token usage ($)
- **Time elapsed**: Wall clock time
- **Smoke test result**: Pass/fail
- **Quality scores**: From LLM-as-judge (see Pillar 2)

---

## Pillar 2: Quality Gates

### LLM-as-Judge

Use Claude to evaluate quality of generated artifacts:

```python
def evaluate_idea_brief(brief_content: str) -> QualityScore:
    """Use Claude to score an idea brief."""
    prompt = f"""
    Rate this idea brief on a scale of 1-10 for each criterion:

    1. Clarity: Is the problem clearly defined?
    2. Completeness: Are all key aspects covered?
    3. Feasibility: Is the scope realistic?
    4. Technical Depth: Are technical considerations addressed?

    BRIEF:
    {brief_content}

    Output JSON: {{"clarity": X, "completeness": X, "feasibility": X, "technical_depth": X}}
    """
    return llm_evaluate(prompt)
```

### Quality Checkpoints

| Phase | Artifact | Evaluation |
|-------|----------|------------|
| Ideation Q&A | Conversation | Questions relevant? Clarifications gathered? |
| Idea Brief | Document | Clear, complete, feasible? |
| Product Spec | Document | Technical depth, coherent architecture? |
| Flight Plan | Waypoints | Appropriate granularity? Dependencies correct? |
| Waypoint Execution | Code | Follows spec? Clean? Tested? |
| Final Product | Application | Functional? Matches original idea? |

### Human Evaluation Protocol

For subjective quality that LLM-as-judge can't fully capture:

1. **Periodic Reviews**: Every N benchmark runs, human reviews sample outputs
2. **A/B Comparisons**: Compare prompt variants on same benchmark
3. **Regression Detection**: Flag quality drops vs historical baseline

---

## Pillar 3: Output Validation

Layered approach from automated to human:

### Layer 1: Smoke Tests (Automated)

```bash
# Each benchmark has a smoke_test.sh
cd $GENERATED_PROJECT
uv sync 2>&1 | grep -v "^$"
uv run pytest --tb=short
uv run ruff check .
uv run mypy src/ --ignore-missing-imports
```

Pass criteria: Exit code 0 for all commands.

### Layer 2: Acceptance Criteria Verification (Automated + LLM)

Each waypoint has acceptance criteria. Verify they're met:

```python
def verify_acceptance_criteria(waypoint: Waypoint, project_path: Path) -> list[bool]:
    """Check if each acceptance criterion is satisfied."""
    results = []
    for criterion in waypoint.acceptance_criteria:
        # Use LLM to evaluate if criterion is met by examining code
        prompt = f"""
        Given this codebase: {get_code_summary(project_path)}

        Is this acceptance criterion satisfied?
        Criterion: "{criterion}"

        Answer: YES or NO, with brief explanation.
        """
        results.append(llm_evaluate_criterion(prompt))
    return results
```

### Layer 3: Human Review (On-demand)

For critical benchmarks or before releases:
- Review generated code for patterns, security, maintainability
- Validate UX flow makes sense
- Check that generated product matches user intent

---

## Self-Hosting Test (The Ultimate Benchmark)

**Goal**: Waypoints builds Waypoints.

### Setup

```yaml
# benchmarks/L5-waypoints-self/input/idea.txt
Build an AI-native software development TUI called Waypoints.
It should guide users from idea to working software through phases:
ideation, product spec, flight planning, and execution.
The system should use Claude for LLM integration and Textual for TUI.
```

### Success Criteria

1. All phases complete without human intervention
2. Generated code passes existing Waypoints test suite
3. Generated app can run through L0-L3 benchmarks
4. Human review confirms structural similarity to actual Waypoints

### Value

- If this works: Strong confidence in system capability
- If this fails: Reveals gaps in prompts, charting, or execution
- Either way: Invaluable learning about system limits

---

## Traditional Testing (Code We Control)

For the Waypoints codebase itself (not LLM outputs):

### Unit Tests (70%)
- Models: Project, Waypoint, FlightPlan serialization
- Git: Receipt validation, service operations
- Executor: `_build_prompt`, `_needs_intervention`, completion detection

### Integration Tests (20%)
- TUI screens with Textual Pilot API
- Phase transitions
- File persistence roundtrips

### E2E Tests (10%)
- Mocked LLM responses for consistent paths
- State machine transitions in FlyScreen

### Test Infrastructure

```bash
# Fast tests (every commit)
uv run pytest -m "not slow" --timeout=30

# Full suite (CI on main)
uv run pytest --timeout=120

# Benchmark runs (scheduled/manual)
python scripts/run_benchmarks.py --level L0-L4
```

---

## Feedback Loops

```
                    ┌─────────────────┐
                    │   Benchmark     │
                    │   Results       │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │  Prompt  │  │   UX     │  │  Code    │
        │ Tuning   │  │ Fixes    │  │  Fixes   │
        └────┬─────┘  └────┬─────┘  └────┬─────┘
             │             │             │
             └─────────────┴─────────────┘
                           │
                    ┌──────▼──────┐
                    │   Re-run    │
                    │  Benchmarks │
                    └─────────────┘
```

Key insight: Benchmark results inform prompt engineering, UX improvements, and code fixes - creating a continuous improvement cycle.

---

## Artifact Verification

Ensure audit trail is complete:

```python
def verify_artifacts(project: Project) -> ArtifactReport:
    """Verify all expected artifacts exist and are valid."""
    required = {
        "project.json": check_valid_json,
        "docs/idea-brief.md": check_non_empty,
        "docs/product-spec.md": check_non_empty,
        "flight-plan.jsonl": check_valid_jsonl,
        "sessions/*.jsonl": check_session_structure,
        "receipts/*.json": check_valid_receipts,
    }

    return ArtifactReport(
        missing=[f for f, check in required.items() if not exists_and_valid(f, check)],
        complete=all_valid(required),
    )
```

---

## Implementation Roadmap

### Phase 1: Foundation
1. Create `benchmarks/` directory structure
2. Implement L0-L1 benchmarks (hello world, todo CLI)
3. Create smoke test runner

### Phase 2: Quality Gates
4. Implement LLM-as-judge for idea briefs and specs
5. Create acceptance criteria verifier
6. Set up quality score tracking

### Phase 3: Self-Hosting
7. Create L5 benchmark (Waypoints builds Waypoints)
8. Document capability gaps found
9. Iterate on prompts based on findings

### Phase 4: CI Integration
10. Add benchmark runs to CI (scheduled, not every PR)
11. Set up quality score dashboards
12. Create regression alerts

---

## Key Metrics to Track

| Metric | Target | Measurement |
|--------|--------|-------------|
| L0-L2 pass rate | 100% | Smoke tests pass |
| L3-L4 pass rate | >80% | Smoke tests pass |
| L5 (self) completion | Goal: 1 success | Full run without intervention |
| Avg iterations/waypoint | <3 | Lower = better prompt effectiveness |
| Cost per benchmark | Track | Optimization opportunity |
| Quality score trend | Improving | LLM-as-judge scores over time |

---

## Summary

Traditional testing alone can't validate an AI-native development system. This strategy combines:

1. **Benchmark Suite**: Objective measure of capability across project types
2. **LLM-as-Judge**: Scalable quality evaluation for subjective artifacts
3. **Output Validation**: Layered verification from smoke tests to human review
4. **Self-Hosting Test**: Ultimate validation - can it build itself?
5. **Traditional Tests**: For the code we control (TUI, models, git)

The feedback loop from benchmarks → improvements → re-run creates a continuous validation and improvement cycle.
