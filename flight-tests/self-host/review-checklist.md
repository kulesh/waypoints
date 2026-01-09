# Self-Hosting Review Checklist

Use this checklist when running Waypoints on itself (building Waypoints v2).

## Pre-Run Safety Check

- [ ] Dev tree is clean: `git status` in dev tree shows no changes
- [ ] Flight test directory exists and is empty

## Setup

```bash
# Create isolated flight test directory
mkdir -p ~/flight-tests/waypoints-self-host

# Run from source with artifacts going to flight test directory
uv run --directory /Users/kulesh/dev/waypoints waypoints \
    --workdir ~/flight-tests/waypoints-self-host
```

---

## Phase: SPARK (Ideation)

**Input**: Project name + idea text

| Check | Pass/Fail | Notes |
|-------|-----------|-------|
| Project name accepted | | |
| Idea text accepted | | |
| Transitioned to Q&A phase | | |

**Artifacts created**:
- [ ] `.waypoints/projects/{slug}/project.json`

---

## Phase: SHAPE (Q&A)

**Evaluate the Q&A dialogue**:

| Check | Pass/Fail | Notes |
|-------|-----------|-------|
| Questions are relevant to building a TUI app | | |
| Questions clarify scope appropriately | | |
| No repetitive or circular questions | | |
| Dialogue feels natural | | |
| Ctrl+D transitions to brief generation | | |

**Artifacts created**:
- [ ] `.waypoints/projects/{slug}/sessions/ideation-*.jsonl`

---

## Phase: SHAPE (Idea Brief)

**Evaluate the generated brief**:

| Check | Pass/Fail | Notes |
|-------|-----------|-------|
| Core Concept clearly defined | | |
| Target User identified | | |
| Key Differentiators listed | | |
| Technical Constraints mentioned | | |
| Success Criteria defined | | |
| Brief resembles actual Waypoints | | |

**Artifacts created**:
- [ ] `.waypoints/projects/{slug}/docs/idea-brief-*.md`

---

## Phase: SHAPE (Product Spec)

**Evaluate the generated spec**:

| Check | Pass/Fail | Notes |
|-------|-----------|-------|
| Vision section complete | | |
| Features described in detail | | |
| Data model defined | | |
| Architecture coherent | | |
| Technical depth appropriate | | |
| Resembles actual Waypoints architecture | | |

**Artifacts created**:
- [ ] `.waypoints/projects/{slug}/docs/product-spec-*.md`

---

## Phase: CHART (Waypoint Generation)

**Evaluate the flight plan**:

| Check | Pass/Fail | Notes |
|-------|-----------|-------|
| Waypoints cover key features | | |
| Granularity appropriate (not too big/small) | | |
| Dependencies make sense | | |
| Acceptance criteria are clear | | |
| Epics broken down appropriately | | |
| No cycles in dependency graph | | |

**Waypoint count**: ___
**Epic count**: ___

**Artifacts created**:
- [ ] `.waypoints/projects/{slug}/flight-plan.jsonl`

---

## Phase: FLY (Execution)

**For each executed waypoint**:

| Waypoint ID | Title | Iterations | Result | Notes |
|-------------|-------|------------|--------|-------|
| WP-001 | | | Pass/Fail | |
| WP-002 | | | Pass/Fail | |
| WP-003 | | | Pass/Fail | |
| ... | | | | |

**Overall execution metrics**:
- Total waypoints attempted: ___
- Successful: ___
- Failed: ___
- Average iterations per waypoint: ___

**Artifacts created**:
- [ ] `.waypoints/projects/{slug}/receipts/*.json`
- [ ] Generated source code in `src/`
- [ ] Generated tests in `tests/`

---

## Output Validation

**Smoke tests on generated code**:

```bash
cd ~/flight-tests/waypoints-self-host
uv sync
uv run pytest
uv run ruff check .
```

| Check | Pass/Fail | Notes |
|-------|-----------|-------|
| `uv sync` succeeds | | |
| Tests pass | | |
| Linting passes | | |
| Application runs | | |

---

## Summary

**Date**: ___
**Duration**: ___
**Total cost**: $___

**Success criteria**:
- [ ] All phases complete without crashes
- [ ] Generated spec resembles actual Waypoints architecture
- [ ] At least 3 waypoints execute successfully
- [ ] Issues documented

**Issues found** (create beads tickets):

1. ___
2. ___
3. ___

**Observations**:

___

---

## Next Steps

After completing this review:
1. Copy this checklist to `flight-tests/self-host/results/{date}-review.md`
2. Create beads tickets for issues found
3. Archive the generated project for reference
