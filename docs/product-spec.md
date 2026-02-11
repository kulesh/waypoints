# Waypoints Product Specification

> Status note (2026-02-11): this is the original product-spec and MVP framing
> document. For the authoritative implemented-state view, see
> `docs/current-state.md`.

## Introduction & Vision

### The Problem with Software Development Today

Traditional software development treats the journey from idea to product as a series of disconnected activities: brainstorming in one tool, documenting in another, tracking tasks in a third, coding in an IDE, and versioning through git commands. Developers context-switch constantly, lose momentum translating between these silos, and spend cognitive energy on orchestration rather than creation.

The fundamental assumption has been that *humans must manage the development process while occasionally using AI as an assistant*. We invert this: **AI orchestrates the development journey while humans provide vision, judgment, and creative direction.**

### Vision Statement

**Waypoints transforms software development from a task-management exercise into a guided journey where developers focus on *what* they want to build while AI handles *how* to get there.**

Like an aircraft's flight management system, Waypoints takes a destination (your product idea), collaborates with the pilot (you) to chart waypoints, then engages autopilot for the journey—with the pilot always able to take control.

### Value Proposition

- **For solo developers** who have ideas but get bogged down in process
- **Waypoints** is an AI-native development environment
- **That** turns ideas into working software through guided collaboration
- **Unlike** traditional IDEs and project management tools
- **Our product** treats development as a continuous journey rather than disconnected tasks, with AI as co-pilot rather than assistant

---

## Goals & Objectives

### High-Level Goals

1. **Reduce idea-to-code friction**: Eliminate the cognitive overhead of translating vision into implementation plans
2. **Maintain creative momentum**: Keep developers in flow state by handling process orchestration
3. **Create traceable journeys**: Every line of code connects back to the intent that spawned it
4. **Enable confident delegation**: Developers trust the autopilot because they shaped the flight plan

### Original MVP Objectives

At project inception, MVP focused on **Steps 1-3: Ideation through Waypoint
Planning**, with execution (Step 4) scheduled post-MVP.

| Objective | Success Criteria |
|-----------|------------------|
| Idea crystallization | Developer can go from vague idea to clear brief in < 30 minutes |
| Spec generation | Product spec is detailed enough for a new developer to understand the project |
| Waypoint planning | Waypoints are appropriately sized and sequenced; developer feels confident in the plan |
| State continuity | Developer can close Waypoints, reopen, and resume exactly where they left off |

---

## Personas

### Primary: The Solo Builder

**Maya, 32, Senior Developer at a startup**

Maya has been coding for 10 years and has dozens of side project ideas in her notes app. She's technically skilled but struggles to maintain momentum on personal projects. By the time she's set up a project, created issues, and planned sprints, her enthusiasm has waned.

**Current frustrations:**
- "I spend more time organizing than building"
- "My ideas feel clear in my head but messy when I try to document them"
- "I start strong but lose track of where I was after a few days away"

**Latent needs Maya doesn't express:**
- She wants to feel like she has a collaborator, not just tools
- She craves the feeling of progress without the overhead of tracking it
- She wants her past decisions to be remembered and respected

**How Waypoints serves Maya:**
- Conversation feels like talking to a thoughtful co-founder
- Progress is visible and automatic
- Returning to a project feels like resuming a conversation, not parsing task lists

### Secondary: The Technical Founder (Post-MVP)

**Alex, 28, Non-technical founder learning to code**

Alex has a clear product vision but limited development experience. Traditional tools assume expertise Alex doesn't have.

**How Waypoints will serve Alex (future):**
- Waypoints doesn't assume prior knowledge of development workflows
- The system explains *why* it's suggesting certain waypoints
- Alex can focus on product decisions while AI handles technical decomposition

---

## User Experience

### Design Principles

1. **Conversation over Configuration**: Settings emerge from discussion, not forms
2. **Journey over Tasks**: Progress is spatial (a path forward) not numerical (5/12 tasks)
3. **Context is Preserved**: Every interaction remembers what came before
4. **Graceful Control Transfer**: Human can take the wheel at any moment without disruption
5. **Visible Thinking**: AI's reasoning is transparent, not magical

### User Journey: From Idea to Flight Plan

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         THE WAYPOINTS JOURNEY                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐   │
│   │          │      │          │      │          │      │          │   │
│   │  SPARK   │ ───► │  SHAPE   │ ───► │  CHART   │ ───► │  FLY     │   │
│   │          │      │          │      │          │      │          │   │
│   └──────────┘      └──────────┘      └──────────┘      └──────────┘   │
│                                                                         │
│   Enter your        Refine through    Generate          Execute         │
│   idea              Q&A dialogue      waypoints         (originally      │
│                     → Idea Brief      → Flight Plan     post-MVP)       │
│                     → Product Spec                                      │
│                                                                         │
│   ─────────────────────── MVP SCOPE ───────────────────────             │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Phase 1: SPARK (Idea Entry)

The developer launches Waypoints and sees a minimal, focused interface:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WAYPOINTS                                                    [?] [⚙]  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│                                                                         │
│                     What do you want to build?                          │
│                                                                         │
│    ┌─────────────────────────────────────────────────────────────────┐  │
│    │ An IDE for generative software development...                  │  │
│    │                                                                 │  │
│    └─────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│                         [Begin Journey →]                               │
│                                                                         │
│  ─────────────────────────────────────────────────────────────────────  │
│  Recent Journeys:                                                       │
│    • waypoints (in progress - CHART phase)                              │
│    • habit-tracker (completed)                                          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Phase 2: SHAPE (Idea Refinement)

After entering the idea, Waypoints engages in Socratic dialogue to crystallize the vision:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WAYPOINTS › SHAPE                                    [Idea Brief] [⚙]  │
├─────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────┬───────────────────────────────┐ │
│ │ DIALOGUE                            │ BRIEF (Draft)                 │ │
│ ├─────────────────────────────────────┼───────────────────────────────┤ │
│ │                                     │ # Generative IDE              │ │
│ │ You: An IDE for generative          │                               │ │
│ │ software development                │ ## Core Concept               │ │
│ │                                     │ An AI-native development      │ │
│ │ Waypoints: Interesting! When you    │ environment that...           │ │
│ │ say "generative," what do you       │                               │ │
│ │ envision the AI generating?         │ ## Target User                │ │
│ │                                     │ Solo developers who...        │ │
│ │ • Code from specifications?         │                               │ │
│ │ • Specifications from ideas?        │ ## Key Differentiators        │ │
│ │ • Both in a continuous flow?        │ • ?                           │ │
│ │                                     │ • ?                           │ │
│ │ You: Both - I want to go from       │                               │ │
│ │ idea to working code                │ ┌───────────────────────────┐ │ │
│ │                                     │ │ ░░░░░░░░░░░░░░░          │ │ │
│ │ Waypoints: That's ambitious and     │ │ Clarity: 45%              │ │ │
│ │ exciting. Let's dig into the        │ │                           │ │ │
│ │ journey between idea and code...    │ │ Missing:                  │ │ │
│ │                                     │ │ • Technical constraints   │ │ │
│ │ ┌─────────────────────────────────┐ │ │ • MVP boundaries          │ │ │
│ │ │ Type your response...           │ │ │ • Success criteria        │ │ │
│ │ └─────────────────────────────────┘ │ └───────────────────────────┘ │ │
│ └─────────────────────────────────────┴───────────────────────────────┘ │
│                                                                         │
│  [← Back]                              [Finalize Brief →] (when ready)  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key UX elements:**
- **Live Brief**: Right panel updates in real-time as understanding crystallizes
- **Clarity Meter**: Visual indicator of completeness, with specific gaps identified
- **Quick Responses**: Suggested answers speed up common responses
- **Editable Brief**: Developer can directly edit the draft at any time

### Phase 2b: Product Specification

Once the brief reaches sufficient clarity, Waypoints generates a detailed product spec:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WAYPOINTS › SHAPE › SPEC                         [Edit] [Export] [⚙]  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ # Waypoints Product Specification                                 │  │
│  │                                                                   │  │
│  │ ## Vision                                                         │  │
│  │ Waypoints transforms software development from a task-management │  │
│  │ exercise into a guided journey...                                 │  │
│  │                                                                   │  │
│  │ ## Target User                                                    │  │
│  │ Solo developers who have ideas but get bogged down in process... │  │
│  │                                                                   │  │
│  │ ## Core Features                                                  │  │
│  │ ### Idea Crystallization                                          │  │
│  │ - Q&A dialogue to refine vague ideas into clear briefs           │  │
│  │ - Real-time brief generation with clarity tracking               │  │
│  │ ...                                                               │  │
│  │                                                                   │  │
│  │ ## FAQ                                                            │  │
│  │ **Q: What happens if tests fail repeatedly?**                    │  │
│  │ A: Waypoints stops and requests human intervention...            │  │
│  │                                                                   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  [← Back to Brief]                              [Generate Waypoints →]  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Phase 3: CHART (Waypoint Generation)

Waypoints analyzes the spec and generates a flight plan:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WAYPOINTS › CHART                              [Spec] [Export] [⚙]    │
├─────────────────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────┬───────────────────────────────┐ │
│ │ FLIGHT PLAN                         │ WAYPOINT DETAILS              │ │
│ ├─────────────────────────────────────┼───────────────────────────────┤ │
│ │                                     │                               │ │
│ │  ◉ WP-001: Project Setup            │ # WP-002: TUI Framework       │ │
│ │  │  Initialize repo, dependencies   │                               │ │
│ │  │                                  │ ## Objective                  │ │
│ │  ▼                                  │ Set up Textual-based TUI      │ │
│ │  ◎ WP-002: TUI Framework ←─selected │ with basic navigation         │ │
│ │  │  Textual setup, basic screens   │                               │ │
│ │  │                                  │ ## Acceptance Criteria        │ │
│ │  ▼                                  │ □ App launches without error  │ │
│ │  ○ WP-003: Idea Entry               │ □ Three screens navigable     │ │
│ │  │  Spark phase UI                  │ □ State persists across       │ │
│ │  │                                  │   screens                     │ │
│ │  ▼                                  │                               │ │
│ │  ◇ WP-004: Q&A Engine [EPIC]        │ ## Estimated Scope            │ │
│ │  │                                  │ ~200 lines, 2-3 files         │ │
│ │  ├─○ WP-004a: Dialogue Manager      │                               │ │
│ │  ├─○ WP-004b: Brief Generator       │ ## Dependencies               │ │
│ │  └─○ WP-004c: Clarity Scoring       │ Depends on: WP-001            │ │
│ │  │                                  │ Blocks: WP-003                │ │
│ │  ▼                                  │                               │ │
│ │  ○ WP-005: Spec Generation          │ ┌───────────────────────────┐ │ │
│ │     ...                             │ │ [Break Down] [Merge With] │ │ │
│ │                                     │ │ [Edit]      [Delete]      │ │ │
│ │ ────────────────────────────────    │ └───────────────────────────┘ │ │
│ │ Legend:                             │                               │ │
│ │ ◉ Complete  ◎ Selected  ○ Pending   │                               │ │
│ │ ◇ Epic (multi-hop)                  │                               │ │
│ └─────────────────────────────────────┴───────────────────────────────┘ │
│                                                                         │
│  [← Back to Spec]    [Refine Plan]              [Ready for Takeoff →]   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key UX elements:**
- **Visual Hierarchy**: Epics (multi-hop) visually contain their sub-waypoints
- **Dependency Visualization**: Clear flow showing what blocks what
- **Inline Actions**: Break down, merge, edit, delete waypoints
- **Selection Detail**: Right panel shows full context for selected waypoint
- **Progress Indicators**: Visual distinction between complete, current, and pending

### Keyboard-First Navigation

As a TUI, keyboard efficiency is paramount:

| Key | Action |
|-----|--------|
| `j/k` or `↑/↓` | Navigate waypoints |
| `Enter` | Select/expand waypoint |
| `e` | Edit selected waypoint |
| `b` | Break down into sub-waypoints |
| `m` | Merge with adjacent waypoint |
| `Tab` | Switch panels |
| `?` | Help overlay |
| `q` | Quit (with save prompt) |

---

## Feature Requirements

### F1: Project Initialization

**Description**: Create or resume a Waypoints project

**Acceptance Criteria**:
- [ ] `waypoints` command with no args shows project selector/creator
- [ ] `waypoints <path>` opens existing project or creates new at path
- [ ] New projects initialize git repo if not present
- [ ] Existing projects load state from `.waypoints/` directory

### F2: Idea Entry (SPARK)

**Description**: Capture initial idea with minimal friction

**Acceptance Criteria**:
- [ ] Single text input for idea entry
- [ ] Ideas stored immediately (no data loss on crash)
- [ ] Recent projects visible for quick resume
- [ ] Transition to SHAPE phase on "Begin Journey"

### F3: Idea Refinement Dialogue (SHAPE)

**Description**: Socratic Q&A to crystallize the idea

**Acceptance Criteria**:
- [ ] AI asks clarifying questions based on gaps in understanding
- [ ] Conversation history persisted and scrollable
- [ ] Quick-reply suggestions for common responses
- [ ] User can type free-form responses
- [ ] Conversation can be paused and resumed

**AI Behavior**:
- Questions should be specific, not generic
- AI should explain *why* it's asking each question
- AI should acknowledge and build on user responses
- Maximum 10-15 questions before suggesting brief finalization

### F4: Live Brief Generation

**Description**: Real-time document synthesis from dialogue

**Acceptance Criteria**:
- [ ] Brief panel updates after each exchange
- [ ] Clarity meter shows percentage complete
- [ ] Missing elements explicitly listed
- [ ] User can directly edit brief text
- [ ] Edits are preserved across dialogue turns

**Brief Sections**:
1. Core Concept (what is it?)
2. Target User (who is it for?)
3. Key Differentiators (why this over alternatives?)
4. Technical Constraints (what are the boundaries?)
5. Success Criteria (how do we know it works?)

### F5: Product Spec Generation

**Description**: Transform brief into detailed specification

**Acceptance Criteria**:
- [ ] One-click generation from finalized brief
- [ ] Spec includes all standard sections (vision, features, data model, etc.)
- [ ] FAQ section addresses likely developer questions
- [ ] User can edit spec in-app (or via $EDITOR for MVP)
- [ ] Spec saved as Markdown in `.waypoints/docs/product-spec.md`

### F6: Waypoint Generation (CHART)

**Description**: Decompose spec into executable waypoints

**Acceptance Criteria**:
- [ ] AI generates waypoints from product spec
- [ ] Each waypoint has: ID, title, objective, acceptance criteria, dependencies
- [ ] Multi-hop waypoints (epics) contain sub-waypoints
- [ ] Dependency graph is valid (no cycles)
- [ ] Waypoints stored in `.waypoints/flight-plan.jsonl`

**Granularity Heuristics**:
- Each waypoint must be independently testable
- Single-hop: one cohesive component/feature that can be verified in isolation
- Multi-hop (epic): feature requires multiple complicated components, each needing separate testing
- Rule of thumb: if a waypoint would require testing multiple distinct behaviors, break it down

### F7: Waypoint Manipulation

**Description**: User can adjust the flight plan

**Acceptance Criteria**:
- [ ] Select waypoint to view details
- [ ] Edit waypoint title, objective, acceptance criteria
- [ ] Break down waypoint into sub-waypoints (AI-assisted)
- [ ] Merge adjacent waypoints (AI-assisted)
- [ ] Reorder waypoints (respecting dependencies)
- [ ] Delete waypoint (with dependency warning)

### F8: State Persistence

**Description**: All state survives app close/reopen

**Acceptance Criteria**:
- [ ] Current phase persisted
- [ ] Dialogue history persisted
- [ ] Brief/spec documents persisted
- [ ] Waypoint state persisted
- [ ] App reopens to exact previous state

### F9: Document Editing

**Description**: Edit generated documents

**MVP Acceptance Criteria**:
- [ ] `e` key opens document in `$EDITOR`
- [ ] Changes saved on editor close
- [ ] App refreshes display after edit

**Post-MVP**:
- [ ] In-TUI markdown editor
- [ ] Live preview
- [ ] Vim keybindings option

### F10: Export & Commit

**Description**: Persist artifacts and mark milestones

**Acceptance Criteria**:
- [ ] All documents saved as Markdown
- [ ] Waypoints saved as JSONL with metadata
- [ ] Git commit created at phase transitions
- [ ] Git tag created when flight plan is finalized
- [ ] Commit messages are descriptive and consistent

---

## Data Model (Conceptual)

### Core Entities

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           DATA MODEL                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐    │
│  │   Project    │────────▶│    Phase     │────────▶│   Artifact   │    │
│  └──────────────┘    1:N  └──────────────┘    1:N  └──────────────┘    │
│         │                        │                                      │
│         │                        │                                      │
│         ▼                        ▼                                      │
│  ┌──────────────┐         ┌──────────────┐                             │
│  │   Dialogue   │         │   Waypoint   │◀────────┐                   │
│  │    History   │         └──────────────┘         │                   │
│  └──────────────┘                │                 │ parent/child      │
│                                  │                 │                   │
│                                  └─────────────────┘                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Project

```json
{
  "id": "uuid",
  "name": "waypoints",
  "created_at": "2025-01-07T10:00:00Z",
  "updated_at": "2025-01-07T14:30:00Z",
  "current_phase": "chart",
  "idea": "An IDE for generative software development"
}
```

### Dialogue Turn

```json
{
  "id": "uuid",
  "project_id": "uuid",
  "phase": "shape",
  "role": "assistant|user",
  "content": "What do you envision the AI generating?",
  "timestamp": "2025-01-07T10:05:00Z",
  "suggestions": ["Code from specs", "Specs from ideas", "Both"]
}
```

### Waypoint

```json
{
  "id": "WP-001",
  "project_id": "uuid",
  "title": "Project Setup",
  "objective": "Initialize repository with dependencies and tooling",
  "acceptance_criteria": [
    "Git repository initialized",
    "pyproject.toml configured",
    "Dev dependencies installed"
  ],
  "parent_id": null,
  "dependencies": [],
  "status": "pending|in_progress|complete",
  "git_tag": null,
  "created_at": "2025-01-07T12:00:00Z",
  "completed_at": null
}
```

### File Structure

```
project/
├── .waypoints/
│   ├── project.json          # Project metadata
│   ├── dialogue.jsonl        # Conversation history
│   ├── flight-plan.jsonl     # Waypoints
│   ├── docs/                 # Human-readable artifacts
│   │   ├── idea.md           # Original idea
│   │   ├── idea-brief.md     # Crystallized brief
│   │   └── product-spec.md   # Full specification
│   └── versions/             # Historical snapshots
│       ├── brief-v1.md
│       ├── brief-v2.md
│       └── spec-v1.md
└── src/                      # Generated code (post-MVP)
```

### XDG Compliance (Production)

For production builds, Waypoints should respect XDG Base Directory Specification:

| Data Type | Location |
|-----------|----------|
| User data | `$XDG_DATA_HOME/waypoints/` (default: `~/.local/share/waypoints/`) |
| Config | `$XDG_CONFIG_HOME/waypoints/` (default: `~/.config/waypoints/`) |
| Cache | `$XDG_CACHE_HOME/waypoints/` (default: `~/.cache/waypoints/`) |

For MVP, we use `.waypoints/` in the project directory for simplicity and portability.

---

## MVP Scope

### In Scope (MVP)

| Feature | Priority | Notes |
|---------|----------|-------|
| Project init/resume | P0 | Core functionality |
| Idea entry (SPARK) | P0 | First user touchpoint |
| Q&A dialogue (SHAPE) | P0 | Core value proposition |
| Live brief generation | P0 | Differentiating feature |
| Clarity tracking | P1 | Guides user through gaps |
| Product spec generation | P0 | Completes SHAPE phase |
| Waypoint generation (CHART) | P0 | Completes MVP journey |
| Waypoint manipulation | P1 | User agency over plan |
| State persistence | P0 | Session continuity |
| $EDITOR integration | P0 | Document editing |
| Git commits at milestones | P1 | Version control |

### Out of Scope (Post-MVP)

| Feature | Phase | Notes |
|---------|-------|-------|
| Autopilot execution (FLY) | v1.1 | Step 4 of journey |
| In-TUI editor | v1.1 | Replace $EDITOR |
| AUAT (automated acceptance) | v1.1 | Part of FLY phase |
| Code-to-waypoint tracing | v1.2 | Start with git blame |
| Team collaboration | v2.0 | Multi-user support |
| Waypoint rewind/fast-forward | v1.2 | Version navigation |

---

## Product Roadmap

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            ROADMAP                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  MVP (v0.1)                v1.0                 v1.1              v2.0  │
│  ─────────                 ────                 ────              ────  │
│                                                                         │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐    ┌────────┐ │
│  │ SPARK       │     │ Polish &    │     │ FLY Phase   │    │ Teams  │ │
│  │ SHAPE       │────▶│ Stability   │────▶│ (Autopilot) │───▶│ Collab │ │
│  │ CHART       │     │             │     │             │    │        │ │
│  └─────────────┘     └─────────────┘     └─────────────┘    └────────┘ │
│                                                                         │
│  • Idea entry          • In-TUI editor    • Test generation   • Shared │
│  • Q&A dialogue        • Refined UX       • Code generation   │ projects│
│  • Brief generation    • Error handling   • AUAT              • Real-  │
│  • Spec generation     • Edge cases       • Auto-commit       │ time   │
│  • Waypoint planning   • Performance      • Progress UI       │ sync   │
│  • Basic persistence   • Documentation    • Intervention      │        │
│                                           │ handling          │        │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Shared Lexicon

| Term | Definition |
|------|------------|
| **Waypoint** | A discrete, achievable milestone in the development journey. Analogous to a GPS waypoint on a flight path. |
| **Flight Plan** | The complete sequence of waypoints from idea to product. |
| **Multi-hop Waypoint** | A waypoint that contains sub-waypoints (like an Epic contains Stories). |
| **Single-hop Waypoint** | An atomic waypoint that can be completed without decomposition. |
| **SPARK** | The phase where an idea is first captured. |
| **SHAPE** | The phase where an idea is refined through dialogue into a brief and spec. |
| **CHART** | The phase where a spec is decomposed into waypoints. |
| **FLY** | The phase where waypoints are executed (implemented in runtime). |
| **LAND** | The completion state when all waypoints are done. |
| **Brief** | A concise document capturing the crystallized idea. |
| **Clarity Score** | A percentage indicating how complete the brief is. |
| **Autopilot** | The AI-driven execution mode that implements waypoints. |
| **Intervention** | Human takeover when autopilot encounters issues. |
| **Journey** | The complete lifecycle of a project in Waypoints. |

---

## Success Metrics

### MVP Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Idea-to-waypoints time | < 1 hour | Time from SPARK to CHART complete |
| Brief clarity achieved | > 80% | Average clarity score at finalization |
| Waypoint adjustment rate | < 30% | % of waypoints user modifies significantly |
| Session resume success | 100% | Projects reopen to correct state |
| User completes journey | > 70% | % of started projects reaching CHART |

### Post-MVP Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Waypoint completion rate | > 90% | % of waypoints completed by autopilot without intervention |
| Code-spec traceability | 100% | All code lines traceable to waypoint |
| Time to working prototype | 50% reduction | vs. traditional development |

---

## Open Questions and Assumptions

### Open Questions

1. **Conflict resolution**: When user edits conflict with AI understanding, how do we reconcile?

2. **Pause semantics**: What exactly is preserved vs. reset when pausing mid-dialogue?

### Assumptions

1. **User has basic git knowledge**: We abstract git complexity but assume familiarity with concepts like commits.

2. **Anthropic API availability**: MVP depends on Claude API access; no offline fallback. Offline support is explicitly out of scope.

3. **Single user per project**: MVP does not handle concurrent editors.

4. **Modern terminal**: User's terminal supports 256 colors and Unicode.

5. **Projects are greenfield**: MVP doesn't handle integration into existing large codebases.

6. **Fixed brief structure**: MVP uses a fixed set of brief sections (Core Concept, Target User, Key Differentiators, Technical Constraints, Success Criteria). Customization deferred post-MVP.

7. **Waypoint granularity**: Each waypoint should be independently testable. Multi-hop waypoints are used when a feature contains multiple complicated components that each warrant separate testing.

---

## FAQ

**Q: Why a TUI instead of a web app or desktop app?**
A: Developers live in the terminal. A TUI integrates naturally into existing workflows, launches instantly, and respects the keyboard-centric interaction model developers prefer. Textual provides rich UI capabilities while maintaining terminal nativity.

**Q: How does Waypoints differ from GitHub Copilot or Cursor?**
A: Those tools assist with code completion within an existing development process. Waypoints reimagines the entire journey from idea to code, treating AI as an orchestrator rather than an assistant. You don't start with code; you start with an idea.

**Q: What if the AI-generated waypoints are wrong?**
A: Waypoints presents a flight plan for human approval. The CHART phase is explicitly a collaboration where users refine, reorder, merge, and break down waypoints. The AI proposes; the human disposes.

**Q: How do you handle API costs?**
A: Users provide their own Anthropic API key. For MVP, we optimize prompt efficiency but don't implement cost controls. Future versions may add usage tracking and budget limits.

**Q: Why JSONL for waypoints instead of a database?**
A: JSONL is human-readable, git-friendly, and requires no additional infrastructure. Each line is a self-contained record, making it easy to append, read, and diff. For a single-user local tool, this simplicity outweighs database benefits.

**Q: How does state persistence work across crashes?**
A: Every user action triggers an immediate write to `.waypoints/`. We use append-only JSONL for dialogue and atomic file writes for documents. On crash, we replay the JSONL to reconstruct state.

**Q: Can I use Waypoints for an existing project?**
A: MVP focuses on greenfield projects. You can run Waypoints in an existing repo, but it won't analyze existing code to generate waypoints. Post-MVP may add "reverse engineering" to create waypoints from existing codebases.

**Q: What happens if I manually edit files in `.waypoints/`?**
A: Waypoints will load whatever it finds. However, structural changes (like invalid JSON) may cause errors. We'll add validation in post-MVP.

---

## Original Prompt

> ## Your Identity
>
> Your name is Jade. Wherever you see your name assume the
> instruction/comment is addressed to you and act accordingly.
>
> ## Your Role
>
> You are a creative, curious, and open-minded product designer specializing
> in AI-native experiences. You have built many products that defy prejudice
> and status quo to create their own category. You approach design by
> questioning fundamental assumptions and reimagining possibilities as if
> prior solutions never existed.
>
> When designing AI-native applications, you think beyond conventional UI
> paradigms and feature sets, instead focusing on how AI can transform core
> user workflows and interactions. People often praise your products for their
> usefulness, exquisite attention to detail, and novel features that surprise
> users by anticipating their needs before they're even expressed.
>
> You excel at identifying outdated patterns in existing products and
> replacing them with more intuitive, AI-enhanced experiences that feel like
> working with a thoughtful collaborator rather than operating a tool. You
> design for symbiosis between human creativity and machine intelligence.
>
> ## Additional Context
>
> We are taking a clean-sheet approach to building a $1. We are assuming that a this has
> never been built and consumers have never used one. Therefore, we can let go
> of any pre-conceived ideas based on the status quo and build an app and an
> experience from the ground up. The environment we are birthing this app
> matters too. We are birthing this app into an environment where knowledge,
> reasoning, and intelligence is readily available to any human or device via
> APIs. Therefore, let go of any prejudice, let go of the status quo, keep an
> open mind and be creative in finding a solution to improve the USER EXPERIENCE with the help of abundant AI
> available around us.
>
> ## Examples
>
> Think beyond traditional interfaces. For example, instead of:
>
> - Creating todo lists → Consider ambient task awareness that surfaces
>   relevant actions at appropriate times
> - Calendar scheduling → Consider intent-based time allocation that adapts to
>   changing priorities
> - Document organization → Consider knowledge-based contextual surfacing of
>   information
>
> Don't feel constrained by these examples, they're meant to illustrate the
> level of reimagining expected.
>
> ## Goal
>
> You are tasked with turning the Idea Brief (which I will provide after this
> prompt) into a detailed Product Specification Document (Product Spec) for an
> AI-native productivity application that reimagines productivity from first
> principles. This product spec will be used by the product development team
> (engineers, designers, data science) to understand the product vision, guide
> system design and architectural decisions for both the Minimum Viable
> Product (MVP) and future iterations.
>
> ## Instruction
>
> When creating this spec, deliberately question conventional productivity
> paradigms. Don't be constrained by how existing productivity tools function
>
> - instead, focus on what users truly need to accomplish and how AI can
>   transform these workflows in ways previously impossible.
>
> The document should be concise yet comprehensive, ideally less than 15 pages
> total, and focus on:
>
> - Introduction & Vision: Product vision and value proposition that
>   challenges traditional productivity assumptions and articulates how this
>   AI-native approach creates a new category
> - Goals & Objectives: Clear high-level goals for the product, primary
>   objectives for the MVP, and how they redefine productivity measurement
> - Personas: Profile of target users focusing not just on their current
>   needs but on latent needs they may not express but would value
>   tremendously
> - User Experience: User journeys that highlight human-AI symbiosis, novel
>   interaction models that transcend traditional UI paradigms, and design
>   principles that prioritize intuitive collaboration between user and AI
> - Feature Requirements: Detailed description of capabilities with
>   acceptance criteria for MVP. Use artifact capabilities to create screen
>   mockups, wireframes, or diagrams wherever necessary, emphasizing how AI
>   transforms traditional productivity workflows
> - Data Model (Conceptual): Identify key data entities the system needs to
>   manage, including what information the AI requires to provide
>   transformative value
> - MVP Scope: Clear delineation of features for the initial release,
>   focusing on the critical AI capabilities that demonstrate the product's
>   unique value
> - Product Roadmap: Prioritization and phasing of features up to MVP and
>   beyond MVP, showing a clear evolution of AI-human collaboration
> - Shared Lexicon: A collection of terms that together describe the product
>   and its function precisely to the team. Create new terminology where
>   necessary to articulate novel concepts that don't exist in traditional
>   productivity tools
> - Success Metrics: KPIs that measure not just traditional productivity
>   metrics but new dimensions of effectiveness enabled by AI
> - Open Questions and Assumptions: List any ambiguities in the brief or
>   assumptions made, particularly around user readiness to adopt new
>   AI-driven workflows
> - FAQ: Put yourself in the shoes of the product development team and write
>   a frequently asked/answered questions section that addresses concerns
>   about technical feasibility and adoption of radically new approaches
> - Original Prompt: Please include this prompt word-for-word
> - Sign-off: Please sign-off with your name (e.g. model string), knowledge
>   cutoff, and timestamp
>
> Throughout the document, maintain a balance between revolutionary thinking
> and practical implementation. Challenge conventional productivity paradigms
> while ensuring the vision is technically feasible with current AI
> capabilities.
>
> For all visual elements including wireframes, diagrams, and mockups, use
> your artifact capabilities to create clear, professional visualizations
> that effectively communicate your design concepts.
>
> Ensure the language is clear, concise, and targeted at a technical
> audience. Focus on the what and why, leaving the how (specific
> implementation details) largely to the development team, while providing
> enough detail to guide their technical design. Please output in Markdown
> format.
>
> Wait for me to provide the Idea Brief after reviewing this prompt before
> beginning your response.
>
> Read the idea brief from docs/idea.md

---

## Sign-off

**Author**: Jade (claude-opus-4-5-20251101)
**Knowledge Cutoff**: May 2025
**Generated**: 2025-01-07

---

*This specification represents a starting point for discussion and iteration. The development team should feel empowered to challenge assumptions and propose alternatives where they see opportunities for improvement.*
