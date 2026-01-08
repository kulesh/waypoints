# Self-Hosting Benchmark

This directory contains the infrastructure for testing Waypoints by having it build itself.

## Quick Start (Semi-Automatic)

```bash
# 1. Create isolated benchmark directory (OUTSIDE dev tree!)
mkdir -p ~/benchmarks/waypoints-self-host
cd ~/benchmarks/waypoints-self-host

# 2. Run Waypoints FROM dev tree, execute IN benchmark directory
uv run --directory /Users/kulesh/dev/waypoints waypoints

# 3. Enter project details:
#    Name: "Waypoints V2"
#    Idea: "Build Waypoints - an AI-native software development TUI..."

# 4. Follow the phases, observe results
# 5. Fill out review-checklist.md
```

## Directory Structure

```
benchmarks/self-host/
├── README.md              # This file
├── review-checklist.md    # Manual review criteria
├── run.sh                 # Automation script (Phase 3)
├── report.py              # Results formatting (Phase 3)
└── results/               # Historical results
    └── {timestamp}.json

~/benchmarks/waypoints-self-host/   # Isolated benchmark tree (NOT in dev)
├── .waypoints/projects/waypoints-v2/
├── src/                   # Generated code
├── tests/                 # Generated tests
└── .git/                  # Separate git repo
```

## Safety

**The dev tree must NEVER be modified by self-hosting tests.**

Always verify before running:
- `pwd` shows `~/benchmarks/waypoints-self-host/`, NOT dev tree
- Dev tree is clean: `git status` shows no changes

## Phases

1. **Phase 1 (Now)**: Semi-automatic - run TUI manually, use review checklist
2. **Phase 2 (Next)**: Add CLI automation (`waypoints new`, `waypoints run`)
3. **Phase 3 (Later)**: Fully scripted with CI integration

## Results

After each run:
1. Copy filled checklist to `results/{date}-review.md`
2. Create beads tickets for issues found
3. Archive generated project if notable
