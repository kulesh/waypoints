# Waypoints Journey State Machine

This document describes the journey state machine that tracks a project's progress from initial idea through to working software.

## Overview

The journey state machine consists of **14 states** organized into **5 phases**:

```
SPARK → SHAPE → CHART → FLY → LAND
```

Each phase can have multiple internal states (idle, generating, review, etc.). The state machine enforces valid transitions and provides crash recovery.

## State Diagram

```
                                    ┌─────────────────────────────────────────────────────────────┐
                                    │                                                             │
                                    ▼                                                             │
┌─────────────────────────────────────────────────────────────────────────────────────────────────┼───┐
│  SPARK                                                                                          │   │
│  ┌──────────────┐      ┌──────────────────┐                                                     │   │
│  │  SPARK_IDLE  │─────▶│  SPARK_ENTERING  │                                                     │   │
│  └──────────────┘      └────────┬─────────┘                                                     │   │
│                                 │                                                               │   │
└─────────────────────────────────┼───────────────────────────────────────────────────────────────┘   │
                                  │                                                                   │
                                  ▼                                                                   │
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐│
│  SHAPE                                                                                             ││
│                                                                                                    ││
│  ┌────────────┐    ┌─────────────────────────┐    ┌─────────────────────┐                          ││
│  │  SHAPE_QA  │───▶│  SHAPE_BRIEF_GENERATING │───▶│  SHAPE_BRIEF_REVIEW │◀──┐                      ││
│  └────────────┘    └─────────────────────────┘    └──────────┬──────────┘   │                      ││
│                                                              │              │ (regenerate)         ││
│                                                              │              │                      ││
│                                                              ▼              │                      ││
│                    ┌─────────────────────────┐    ┌─────────────────────┐   │                      ││
│                    │  SHAPE_SPEC_GENERATING  │◀───│  SHAPE_BRIEF_REVIEW │───┘                      ││
│                    └───────────┬─────────────┘    └─────────────────────┘                          ││
│                                │                                                                   ││
│                                ▼                                                                   ││
│                    ┌─────────────────────────┐                                                     ││
│                    │   SHAPE_SPEC_REVIEW     │◀──────────────────────────┐                         ││
│                    └───────────┬─────────────┘                           │ (regenerate)            ││
│                                │                                         │                         ││
└────────────────────────────────┼─────────────────────────────────────────┼─────────────────────────┘│
                                 │                                         │                          │
                                 ▼                                         │                          │
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐│
│  CHART                         │                                         │                         ││
│                                │                                         │                         ││
│  ┌───────────────────────┐     │     ┌─────────────────┐                 │                         ││
│  │   CHART_GENERATING    │◀────┼─────│   CHART_REVIEW  │◀────────────────┤                         ││
│  └───────────┬───────────┘     │     └────────┬────────┘                 │                         ││
│              │                 │              │          ▲               │                         ││
│              │                 │              │          │ (edit plan)   │                         ││
│              └─────────────────┼──────────────┼──────────┼───────────────┘                         ││
│                                │              │          │                                         ││
└────────────────────────────────┼──────────────┼──────────┼─────────────────────────────────────────┘│
                                 │              │          │                                          │
                                 ▼              ▼          │                                          │
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐│
│  FLY                                                     │                                         ││
│                                                          │                                         ││
│  ┌─────────────┐      ┌────────────────┐      ┌─────────┴───────┐                                  ││
│  │  FLY_READY  │◀────▶│  FLY_EXECUTING │◀────▶│   FLY_PAUSED    │────────────────────────────┐     ││
│  └──────┬──────┘      └───────┬────────┘      └─────────────────┘                            │     ││
│         │                     │                         ▲                                    │     ││
│         │                     │                         │                                    │     ││
│         │                     ▼                         │                                    │     ││
│         │             ┌────────────────────┐            │                                    │     ││
│         │             │  FLY_INTERVENTION  │────────────┴────────────────────────────────────┤     ││
│         │             └─────────┬──────────┘                                                 │     ││
│         │                       │                                                            │     ││
│         └───────────────────────┼────────────────────────────────────────────────────────────┘     ││
│                                 │                                                                  ││
└─────────────────────────────────┼──────────────────────────────────────────────────────────────────┘│
                                  │                                                                   │
                                  ▼                                                                   │
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐│
│  LAND                                                                                              ││
│                                                                                                    ││
│  ┌───────────────┐                                                                                 ││
│  │  LAND_REVIEW  │─────────────────────────────────────────────────────────────────────────────────┘│
│  └───────┬───────┘                                                                                  │
│          │                                                                                          │
│          └──────────────────────────────────────────────────────────────────────────────────────────┘
│                        (V2 iteration - back to SPARK_IDLE)
│
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## States Reference

### SPARK Phase (Initial Idea)

| State | Screen | Recoverable | Description |
|-------|--------|-------------|-------------|
| `spark:idle` | ideation | ✓ | Waiting for user to enter an idea |
| `spark:entering` | ideation | ✗ | User is typing their initial idea |

### SHAPE Phase (Idea Refinement)

| State | Screen | Recoverable | Description |
|-------|--------|-------------|-------------|
| `shape:qa` | ideation-qa | ✓ | AI asking clarifying questions about the idea |
| `shape:brief:generating` | idea-brief | ✗ | AI generating the idea brief |
| `shape:brief:review` | idea-brief | ✓ | User reviewing the generated brief |
| `shape:spec:generating` | product-spec | ✗ | AI generating the product specification |
| `shape:spec:review` | product-spec | ✓ | User reviewing the generated spec |

### CHART Phase (Flight Plan Creation)

| State | Screen | Recoverable | Description |
|-------|--------|-------------|-------------|
| `chart:generating` | chart | ✗ | AI generating waypoints (flight plan) |
| `chart:review` | chart | ✓ | User reviewing/editing the flight plan |

### FLY Phase (Execution)

| State | Screen | Recoverable | Description |
|-------|--------|-------------|-------------|
| `fly:ready` | fly | ✓ | Ready to execute, waiting for user to start |
| `fly:executing` | fly | ✗ | Actively executing a waypoint |
| `fly:paused` | fly | ✓ | Execution paused by user |
| `fly:intervention` | fly | ✗ | Execution halted due to error, awaiting user decision |

### LAND Phase (Completion)

| State | Screen | Recoverable | Description |
|-------|--------|-------------|-------------|
| `land:review` | land | ✓ | All waypoints complete, reviewing results |

## Valid Transitions

### Forward Progress

| From | To | Trigger | Code Location |
|------|-----|---------|---------------|
| `spark:idle` | `spark:entering` | User submits idea | `ideation.py:116` |
| `spark:entering` | `shape:qa` | Project created | `ideation_qa.py:154` |
| `shape:qa` | `shape:brief:generating` | Q&A complete | `idea_brief.py:207` |
| `shape:brief:generating` | `shape:brief:review` | Brief generated | `idea_brief.py:280` |
| `shape:brief:review` | `shape:spec:generating` | User accepts brief | `product_spec.py:237` |
| `shape:spec:generating` | `shape:spec:review` | Spec generated | `product_spec.py:300` |
| `shape:spec:review` | `chart:generating` | User accepts spec | `chart.py:276` |
| `chart:generating` | `chart:review` | Waypoints generated | `chart.py:379` |
| `chart:review` | `fly:ready` | User accepts plan (Ctrl+Enter or Ctrl+F) | `chart.py:869, 905` |
| `fly:ready` | `fly:executing` | Start execution (Space or Ctrl+Enter) | `fly.py:1431-1436` |
| `fly:executing` | `fly:paused` | User pauses (Space) | `fly.py:1742` |
| `fly:executing` | `fly:intervention` | Error occurs | `fly.py:1749, 1758, 1773, 1795` |
| `fly:executing` | `land:review` | All waypoints complete | `fly.py:1737` |
| `fly:paused` | `fly:executing` | Resume (Space or Ctrl+Enter) | `fly.py:1463` |

### Regeneration Loops

| From | To | Trigger |
|------|-----|---------|
| `shape:brief:review` | `shape:brief:generating` | User requests regeneration |
| `shape:spec:review` | `shape:spec:generating` | User requests regeneration |
| `chart:review` | `chart:generating` | User requests regeneration |

### Backward Navigation

| From | To | Trigger | Code Location |
|------|-----|---------|---------------|
| `fly:ready` | `chart:review` | Edit plan | `fly.py:1523` |
| `fly:paused` | `chart:review` | Edit plan | `fly.py:1523` |
| `fly:paused` | `fly:ready` | Back to ready | Implicit |
| `fly:intervention` | `chart:review` | Edit plan | `fly.py:1523` |
| `fly:intervention` | `fly:paused` | Skip waypoint | `fly.py:1881` |
| `fly:intervention` | `fly:executing` | Retry | `fly.py:1843` |
| `land:review` | `fly:ready` | Fix issues | `land.py:698` |
| `land:review` | `spark:idle` | Start V2 | `land.py:711` |

## Recovery System

When the application crashes or is forcefully closed during a non-recoverable state, the recovery system moves the project to the nearest safe state on next launch.

### Recovery Map

| Non-Recoverable State | Recovers To |
|----------------------|-------------|
| `spark:entering` | `spark:idle` |
| `shape:brief:generating` | `shape:qa` |
| `shape:spec:generating` | `shape:brief:review` |
| `chart:generating` | `shape:spec:review` |
| `fly:executing` | `fly:ready` |
| `fly:intervention` | `fly:ready` |

## Screen-to-State Mapping

Multiple states can map to the same screen. The screen determines what UI to show, while the state tracks the exact progress.

| Screen Name | States |
|-------------|--------|
| `ideation` | `spark:idle`, `spark:entering` |
| `ideation-qa` | `shape:qa` |
| `idea-brief` | `shape:brief:generating`, `shape:brief:review` |
| `product-spec` | `shape:spec:generating`, `shape:spec:review` |
| `chart` | `chart:generating`, `chart:review` |
| `fly` | `fly:ready`, `fly:executing`, `fly:paused`, `fly:intervention` |
| `land` | `land:review` |

## Resume Behavior

When a project is selected from the project list, the app resumes based on the journey's **phase** (derived from state):

| Phase | Resume Behavior |
|-------|-----------------|
| `ideation` | Show IdeationScreen |
| `ideation-qa` | Resume Q&A with stored idea |
| `idea-brief` | If brief exists, show review screen; otherwise, show ideation |
| `product-spec` | If spec exists, show review screen; otherwise, continue from brief |
| `chart` | Always show ChartScreen (loads existing flight plan if any) |
| `fly` | Show FlyScreen with existing flight plan |
| `land` | Show LandScreen |

**Important:** The resume logic uses the **phase** (screen name), not the exact state. This means:
- `chart:review` → ChartScreen (user can review/edit plan before flying)
- `fly:ready` → FlyScreen (user can start execution)

## Keybindings by Phase

### CHART Phase
- `Ctrl+Enter` - Accept plan and proceed to FLY
- `Ctrl+F` - Forward to FLY (same as Ctrl+Enter)
- `Ctrl+B` - Back to SHAPE

### FLY Phase
- `Space` - Start/Pause execution
- `Ctrl+Enter` - Start/Resume execution
- `Ctrl+F` - Forward to LAND (if all waypoints complete)
- `Ctrl+B` - Back to CHART (edit plan)

### LAND Phase
- `f` - Fix issues (return to FLY)
- `v` - View generative spec
- `r` - Regenerate from spec

## Implementation Notes

### State History

Every transition is recorded in the journey's `state_history` array:

```json
{
  "from": "fly:executing",
  "to": "land:review",
  "at": "2026-01-10T22:27:58.266783+00:00"
}
```

Recovery transitions include an additional `"reason": "recovery"` field.

### Immutable Transitions

The `Journey.transition()` method returns a **new** Journey instance rather than modifying the existing one. The project is responsible for saving the updated journey.

### Migration

Old state names are migrated on load:
- `"landed"` → `"land:review"`
