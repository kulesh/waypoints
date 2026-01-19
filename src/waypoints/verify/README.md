# GenSpec Verification System

Verify that genspecs produce reproducible, functionally equivalent products.

## Quick Start

```bash
# 1. Bootstrap: Create reference artifacts from your genspec
waypoints verify ./my-project --bootstrap

# 2. Later: Verify reproducibility (regenerate and compare)
waypoints verify ./my-project
```

## Commands

### `waypoints verify`

Run the full verification pipeline.

```bash
waypoints verify <genspec-dir> [options]

Options:
  --bootstrap    Create reference from current generation (first run)
  --skip-fly     Skip execution phase (compare artifacts only)
  --verbose, -v  Show detailed progress and LLM output
```

**Bootstrap mode** (`--bootstrap`):
- Generates product spec from idea brief
- Generates flight plan from spec
- Saves all artifacts to `<genspec-dir>/reference/`

**Verify mode** (default):
- Regenerates spec and plan from idea brief
- Compares against reference artifacts using LLM judge
- Outputs verification report to `<genspec-dir>/verify-output/`

### `waypoints compare`

Compare two artifacts directly for semantic equivalence.

```bash
waypoints compare <artifact-a> <artifact-b> [options]

Options:
  --type, -t     Artifact type: "spec" or "plan" (default: spec)
  --verbose, -v  Show streaming LLM output
```

**Exit codes:**
- `0` - Artifacts are equivalent
- `1` - Artifacts are different
- `2` - Uncertain (couldn't determine)

## Directory Structure

After running verification:

```
my-project/
├── docs/
│   └── idea-brief-*.md      # Input: Your idea brief
├── reference/                # Created by --bootstrap
│   ├── idea-brief.md        # Copy of input
│   ├── product-spec.md      # Generated reference spec
│   └── flight-plan.json     # Generated reference plan
└── verify-output/            # Created by verify
    ├── product-spec.md      # Regenerated spec
    ├── flight-plan.json     # Regenerated plan
    └── verification-report.json
```

## Verification Report

The report (`verification-report.json`) contains:

```json
{
  "overall_status": "pass|fail|partial|error",
  "steps": [
    {
      "name": "spec_comparison",
      "status": "pass|fail",
      "result": {
        "verdict": "equivalent|different|uncertain",
        "confidence": 0.95,
        "rationale": "Both specs describe the same features...",
        "differences": []
      }
    },
    {
      "name": "plan_comparison",
      "status": "pass|fail",
      "result": { ... }
    }
  ]
}
```

## Examples

### Verify a project's reproducibility

```bash
# You have a project with an idea brief
ls my-blog-project/docs/
# idea-brief-20240115-143022.md

# Create reference (first time)
waypoints verify ./my-blog-project --bootstrap
# Creates: ./my-blog-project/reference/

# Later, verify it regenerates consistently
waypoints verify ./my-blog-project
# Output: verification-report.json
```

### Compare two specs directly

```bash
# Compare specs from different runs
waypoints compare run1/product-spec.md run2/product-spec.md --type spec

# Output (JSON):
{
  "verdict": "equivalent",
  "confidence": 0.92,
  "rationale": "Both specifications describe the same blog engine...",
  "differences": []
}
```

### Compare flight plans

```bash
waypoints compare plan-a.json plan-b.json --type plan --verbose
```

## How It Works

1. **Input**: Idea brief (fixed starting point, eliminates Q&A variance)

2. **Generation**: Uses the same `JourneyCoordinator` methods as the TUI:
   - `coordinator.generate_product_spec(brief)`
   - `coordinator.generate_flight_plan(spec)`

3. **Comparison**: LLM-based semantic comparison
   - Asks: "Would someone reading both understand they're building the same thing?"
   - Returns structured verdict with confidence score

4. **Verdict**:
   - `equivalent`: Specs/plans describe the same product
   - `different`: Meaningful differences found
   - `uncertain`: Cannot determine with confidence

## V1 Limitations

- **Artifacts only**: Compares specs and plans, not actual products
- **No execution**: Doesn't run the FLY phase or compare built products
- **Same tech stack**: Assumes same language/framework for comparison

Future versions will add:
- Test generation from acceptance criteria
- Execution comparison via generated test suites
