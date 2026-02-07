# Self-Hosting Flight Test

This directory contains the infrastructure for testing Waypoints by having it build itself.

## Quick Start (Semi-Automatic)

```bash
# 1. Launch the self-host harness (artifacts outside dev tree)
./flight-tests/self-host/run.sh ~/flight-tests/waypoints-self-host

# 2. Enter project details:
#    Name: "Waypoints V2"
#    Idea: "Build Waypoints - an AI-native software development TUI..."

# 3. Follow the phases, observe results
# 4. Fill out review-checklist.md
# 5. Summarize checklist progress
python ./flight-tests/self-host/report.py ./flight-tests/self-host/review-checklist.md
```

`run.sh` calls:

```bash
uv run --directory <repo-root> waypoints --workdir <target-dir>
```

This keeps generated artifacts out of the repository working tree.

## Directory Structure

```
flight-tests/self-host/
├── README.md              # This file
├── review-checklist.md    # Manual review criteria
├── run.sh                 # Automation script (Phase 3)
├── report.py              # Results formatting (Phase 3)
└── results/               # Historical results
    └── {timestamp}.json

~/flight-tests/waypoints-self-host/   # Isolated flight test tree (NOT in dev)
├── .waypoints/projects/waypoints-v2/
├── src/                   # Generated code
├── tests/                 # Generated tests
└── .git/                  # Separate git repo
```

## Safety

**The dev tree must NEVER be modified by flight tests.**

Always verify before running:
- Dev tree is clean: `git status` shows no changes

## Phases

1. **Phase 1 (Now)**: Semi-automatic - run TUI manually, use review checklist
2. **Phase 2 (Next)**: Add CLI automation (`waypoints new`, `waypoints run`)
3. **Phase 3 (In Progress)**: Scripted launch + checklist summary helpers
4. **Phase 4 (Later)**: CI integration

## Results

After each run:
1. Copy filled checklist to `results/{date}-review.md`
2. Create beads tickets for issues found
3. Archive generated project if notable
