# Waypoints Architecture Assessment

**Reviewer**: Claude (claude-opus-4-5-20251101)
**Date**: 2026-01-08
**Codebase Version**: 8a1274c (main)

---

## Executive Summary

Waypoints is an ambitious AI-native software development environment that aims to transform how developers turn ideas into working software. The architecture is well-considered, with clean separation of concerns, robust persistence patterns, and thoughtful UX design. However, several areas need strengthening before the product can achieve its full potential.

**Overall Assessment**: Strong foundation with clear vision. Ready for MVP validation but requires architectural hardening for production use.

---

## Table of Contents

1. [Strengths](#strengths)
2. [Weaknesses](#weaknesses)
3. [Architecture Assessment](#architecture-assessment)
4. [Design Recommendations](#design-recommendations)
5. [Feature Roadmap for 100X Impact](#feature-roadmap-for-100x-impact)
6. [Utility Assessment](#utility-assessment)
7. [Risk Analysis](#risk-analysis)
8. [Actionable Next Steps](#actionable-next-steps)

---

## Strengths

### 1. Clean Architectural Separation

The codebase demonstrates excellent separation of concerns:

```
models/     → Pure data structures, no I/O dependencies
llm/        → LLM abstraction layer
git/        → Version control isolation
fly/        → Execution engine
tui/        → Presentation layer
```

Each layer can be tested and evolved independently. The `models/` package has zero dependencies on other packages—a sign of good design.

### 2. Crash-Safe Persistence with JSONL

The choice of JSONL for dialogue history and flight plans is excellent:

- **Append-only writes** prevent data corruption on crashes
- **Line-by-line parsing** enables streaming reads
- **Git-friendly diffs** make changes reviewable
- **Human-readable** format aids debugging

```python
# From models/session.py - atomic append pattern
def append(self, message: Message) -> None:
    with open(self.path, "a") as f:
        f.write(json.dumps(message.to_dict()) + "\n")
```

### 3. Trust-but-Verify Receipt Pattern

The checklist receipt system (`git/receipt.py`) is a novel and important pattern for AI-native development:

```python
@dataclass
class ChecklistReceipt:
    waypoint_id: str
    completed_at: datetime
    checklist: list[ChecklistItem]

    def is_valid(self) -> bool:
        return all(item.status == "passed" for item in self.checklist
                   if item.status != "skipped")
```

This creates an audit trail and prevents the AI from claiming false completion.

### 4. Hierarchical Waypoint Model

The parent-child waypoint structure elegantly handles both simple tasks and complex epics:

```python
# Supports flat lists AND hierarchies
WP-001: Setup project
WP-002: Build authentication [EPIC]
  WP-002a: Create user model
  WP-002b: Implement login flow
  WP-002c: Add session management
WP-003: Build dashboard
```

### 5. Phase-Based Git Commits

Automatic commits at phase boundaries create natural restore points:

| Phase Transition | Commit | Tag |
|-----------------|--------|-----|
| Idea → Brief | `feat({slug}): Complete ideation phase` | `{slug}/idea-brief` |
| Brief → Spec | `feat({slug}): Finalize idea brief` | — |
| Spec → Chart | `feat({slug}): Complete product specification` | `{slug}/spec` |
| Chart → Fly | `feat({slug}): Flight plan ready for takeoff` | `{slug}/ready` |

### 6. Reactive TUI Architecture

The Textual-based UI with message passing enables responsive interfaces during long LLM operations:

```python
class StreamingChunk(Message):
    chunk: str
    message_id: str

# Worker handles LLM call, posts chunks to UI thread
```

---

## Weaknesses

### 1. No Error Recovery in Execution Phase

The `WaypointExecutor` has a hard limit of 10 iterations but no sophisticated recovery:

```python
# fly/executor.py
MAX_ITERATIONS = 10

# What happens when MAX_ITERATIONS is reached?
# Currently: Just stops. No rollback, no partial save, no user intervention flow.
```

**Impact**: Failed waypoints leave the project in an undefined state.

### 2. Single-Threaded LLM Calls

All LLM interactions are synchronous within async wrappers:

```python
# llm/client.py
def stream_message(...) -> Iterator[str]:
    loop = asyncio.new_event_loop()
    # Blocks on each chunk
```

**Impact**: UI can feel sluggish; no parallel waypoint exploration possible.

### 3. No Offline Capability

The system requires constant API connectivity:

```python
# No local model fallback
# No cached responses for common patterns
# No graceful degradation
```

**Impact**: Unusable without internet; no cost control for iterative refinement.

### 4. Limited Waypoint Editing

While the CHART phase allows viewing waypoints, editing capabilities are minimal:

- No drag-and-drop reordering
- No inline objective editing
- No dependency visualization graph
- Break-down requires full LLM round-trip

**Impact**: Users can't easily adjust AI-generated plans to match their preferences.

### 5. No Multi-Project Support

The current architecture assumes one active project:

```python
# tui/app.py
def on_mount(self):
    self.push_screen(IdeationScreen())  # Always starts fresh
```

**Impact**: Can't switch between projects or manage a portfolio of ideas.

### 6. Missing Observability

No metrics, tracing, or cost tracking:

```python
# No token counting
# No cost accumulation
# No timing metrics
# No prompt versioning
```

**Impact**: Can't optimize prompts, predict costs, or debug performance issues.

### 7. Hardcoded Prompts

System prompts are embedded in screen files:

```python
# screens/ideation_qa.py
SYSTEM_PROMPT = """You are a thoughtful product collaborator..."""

# screens/chart.py
WAYPOINT_GENERATION_PROMPT = """Given this product specification..."""
```

**Impact**: Can't A/B test prompts, version them, or let users customize.

---

## Architecture Assessment

### Data Flow Analysis

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CURRENT DATA FLOW                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  User Input → Screen → ChatClient → Claude API → Screen → Display  │
│       │                                              │              │
│       ▼                                              ▼              │
│  SessionWriter ◄─────────────────────────────► DialogueHistory     │
│       │                                                             │
│       ▼                                                             │
│  .waypoints/sessions/*.jsonl                                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Assessment**: Linear and simple, but lacks feedback loops for quality improvement.

### Component Coupling

| Component | Dependencies | Coupling Level |
|-----------|--------------|----------------|
| `models/` | None | ✅ Excellent |
| `llm/` | claude-agent-sdk | ✅ Good (single external) |
| `git/` | subprocess (git) | ✅ Good (standard tool) |
| `fly/` | models, llm, git | ⚠️ Moderate (orchestration) |
| `tui/` | models, llm, fly | ⚠️ High (knows everything) |

**Recommendation**: Extract an `orchestration/` layer between `tui/` and the business logic.

### State Management

Current state is distributed:

- Project metadata → `project.json`
- Dialogue → `sessions/*.jsonl`
- Documents → `docs/*.md`
- Waypoints → `flight-plan.jsonl`
- Receipts → `receipts/*.json`
- App state → In-memory (Textual App)

**Assessment**: Good for persistence, but no unified state machine for phase transitions. Edge cases (e.g., app crash during phase transition) may leave inconsistent state.

---

## Design Recommendations

### 1. Introduce State Machine for Phases

```python
# Proposed: models/journey.py

class JourneyState(Enum):
    SPARK_IDLE = "spark:idle"
    SPARK_ENTERING = "spark:entering"
    SHAPE_QA = "shape:qa"
    SHAPE_BRIEF_GENERATING = "shape:brief:generating"
    SHAPE_BRIEF_REVIEW = "shape:brief:review"
    SHAPE_SPEC_GENERATING = "shape:spec:generating"
    SHAPE_SPEC_REVIEW = "shape:spec:review"
    CHART_GENERATING = "chart:generating"
    CHART_REVIEW = "chart:review"
    FLY_READY = "fly:ready"
    FLY_EXECUTING = "fly:executing"
    FLY_PAUSED = "fly:paused"
    FLY_INTERVENTION = "fly:intervention"
    LANDED = "landed"

@dataclass
class Journey:
    state: JourneyState
    project: Project

    def can_transition(self, target: JourneyState) -> bool:
        return target in VALID_TRANSITIONS[self.state]

    def transition(self, target: JourneyState) -> "Journey":
        if not self.can_transition(target):
            raise InvalidTransition(self.state, target)
        return Journey(state=target, project=self.project)
```

**Benefit**: Explicit state machine prevents invalid transitions and simplifies testing.

### 2. Add Prompt Registry

```python
# Proposed: llm/prompts.py

@dataclass
class Prompt:
    id: str
    version: str
    template: str
    variables: list[str]

    def render(self, **kwargs) -> str:
        return self.template.format(**kwargs)

class PromptRegistry:
    def __init__(self, prompts_dir: Path):
        self.prompts = self._load_prompts(prompts_dir)

    def get(self, prompt_id: str, version: str = "latest") -> Prompt:
        ...

# Usage
registry = PromptRegistry(Path(".waypoints/prompts/"))
prompt = registry.get("waypoint-generation", version="v2")
```

**Benefit**: Version prompts, A/B test, let users customize without code changes.

### 3. Implement Intervention Protocol

```python
# Proposed: fly/intervention.py

class InterventionType(Enum):
    TEST_FAILURE = "test_failure"
    LINT_ERROR = "lint_error"
    TYPE_ERROR = "type_error"
    ITERATION_LIMIT = "iteration_limit"
    USER_REQUESTED = "user_requested"

@dataclass
class Intervention:
    type: InterventionType
    waypoint_id: str
    context: dict[str, Any]
    suggested_actions: list[str]

    def to_prompt(self) -> str:
        """Format intervention for user display."""
        ...

class InterventionHandler:
    async def handle(self, intervention: Intervention) -> InterventionResult:
        # Show modal to user
        # Collect decision (retry, skip, edit, abort)
        # Return result for executor to continue
        ...
```

**Benefit**: Graceful degradation instead of silent failure.

### 4. Add Cost Tracking

```python
# Proposed: llm/metrics.py

@dataclass
class LLMCall:
    prompt_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    timestamp: datetime

class CostTracker:
    def __init__(self, budget_usd: float | None = None):
        self.calls: list[LLMCall] = []
        self.budget = budget_usd

    def record(self, call: LLMCall) -> None:
        self.calls.append(call)
        if self.budget and self.total_cost > self.budget:
            raise BudgetExceeded(self.total_cost, self.budget)

    @property
    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    def summary(self) -> dict:
        return {
            "total_calls": len(self.calls),
            "total_cost_usd": self.total_cost,
            "avg_latency_ms": mean(c.latency_ms for c in self.calls),
        }
```

**Benefit**: Users can set budgets, track spend per project/phase.

### 5. Extract Orchestration Layer

```python
# Proposed: orchestration/coordinator.py

class JourneyCoordinator:
    """Coordinates phase transitions and state management."""

    def __init__(
        self,
        project: Project,
        llm: ChatClient,
        git: GitService,
        prompts: PromptRegistry,
        metrics: CostTracker,
    ):
        self.project = project
        self.journey = Journey.load(project) or Journey.new(project)
        ...

    async def advance_to(self, target_phase: str) -> None:
        """Advance journey to target phase with all side effects."""
        ...

    async def execute_waypoint(self, waypoint_id: str) -> ExecutionResult:
        """Execute single waypoint with full lifecycle."""
        ...
```

**Benefit**: TUI becomes thin presentation layer; logic is testable without UI.

---

## Feature Roadmap for 100X Impact

### Tier 1: Foundation (MVP → v1.0)

| Feature | Impact | Effort | Priority |
|---------|--------|--------|----------|
| **Intervention UI** | Prevents lost work on failures | Medium | P0 |
| **Cost tracking** | Enables budget management | Low | P0 |
| **Multi-project dashboard** | Portfolio management | Medium | P1 |
| **Prompt versioning** | Enables iteration | Low | P1 |
| **State machine** | Prevents edge case bugs | Medium | P1 |

### Tier 2: Differentiation (v1.0 → v1.5)

| Feature | Impact | Effort | Priority |
|---------|--------|--------|----------|
| **Waypoint templates** | Reuse across projects | Medium | P1 |
| **Collaborative editing** | Team workflows | High | P2 |
| **Local model support** | Offline capability, cost reduction | High | P2 |
| **Dependency graph visualization** | Better planning UX | Medium | P2 |
| **Waypoint time estimation** | Project planning | Medium | P2 |

### Tier 3: 100X Multipliers (v1.5 → v2.0)

#### 3.1 Learning System

```
┌─────────────────────────────────────────────────────────────────────┐
│                      LEARNING FEEDBACK LOOP                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   Execution Results                                                 │
│         │                                                           │
│         ▼                                                           │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐            │
│   │   Success   │    │   Failure   │    │   Edits     │            │
│   │   Patterns  │    │   Patterns  │    │   Made      │            │
│   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘            │
│          │                  │                  │                    │
│          └──────────────────┼──────────────────┘                    │
│                             ▼                                       │
│                    ┌─────────────────┐                              │
│                    │  Pattern Store  │                              │
│                    └────────┬────────┘                              │
│                             │                                       │
│                             ▼                                       │
│                    ┌─────────────────┐                              │
│                    │ Prompt Tuning   │                              │
│                    │ Waypoint Sizing │                              │
│                    │ Time Estimates  │                              │
│                    └─────────────────┘                              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Impact**: System gets smarter with use. Waypoint estimates improve. Prompts auto-tune.

#### 3.2 Code Archaeology

When starting Waypoints on an existing codebase:

```python
# Proposed: archaeology/analyzer.py

class CodebaseAnalyzer:
    """Reverse-engineer waypoints from existing code."""

    async def analyze(self, repo_path: Path) -> ArchaeologyReport:
        # 1. Parse git history
        # 2. Identify logical commits
        # 3. Group into features
        # 4. Generate retrospective waypoints
        # 5. Create "as-built" spec
        return ArchaeologyReport(
            inferred_spec=spec,
            waypoints=waypoints,
            confidence=0.85,
        )
```

**Impact**: Onboard existing projects. Understand legacy codebases. Generate missing docs.

#### 3.3 Parallel Waypoint Exploration

```python
# Execute independent waypoints in parallel
async def execute_parallel(self, waypoints: list[Waypoint]) -> list[ExecutionResult]:
    # Identify independent waypoints (no mutual dependencies)
    independent = self._find_independent_sets(waypoints)

    # Execute each set in parallel
    results = []
    for batch in independent:
        batch_results = await asyncio.gather(
            *[self.execute_waypoint(wp) for wp in batch]
        )
        results.extend(batch_results)

    return results
```

**Impact**: 3-5x faster execution for projects with parallelizable waypoints.

#### 3.4 Intelligent Waypoint Sizing

```python
# Proposed: chart/sizer.py

class WaypointSizer:
    """ML-based waypoint complexity estimation."""

    def estimate(self, waypoint: Waypoint, context: ProjectContext) -> SizeEstimate:
        features = self._extract_features(waypoint, context)

        return SizeEstimate(
            complexity=self.model.predict_complexity(features),
            estimated_iterations=self.model.predict_iterations(features),
            confidence=self.model.confidence(features),
            similar_waypoints=self._find_similar(features),
        )
```

**Impact**: Better planning. Automatic epic detection. Realistic project timelines.

#### 3.5 Voice-First Interface

```python
# Proposed: tui/voice.py

class VoiceInterface:
    """Voice input/output for hands-free development."""

    async def listen(self) -> str:
        # Local whisper model for privacy
        ...

    async def speak(self, text: str) -> None:
        # TTS for responses
        ...

    async def dialogue(self) -> AsyncIterator[str]:
        # Full voice conversation loop
        ...
```

**Impact**: Code while walking. Accessibility. Natural ideation flow.

#### 3.6 Waypoint Marketplace

```yaml
# .waypoints/marketplace/auth-jwt.yaml
name: JWT Authentication
author: waypoints-community
version: 1.2.0
waypoints:
  - id: WP-AUTH-001
    title: User model with password hashing
    objective: Create User model with bcrypt password hashing
    acceptance_criteria:
      - User model has email, password_hash fields
      - Password hashing uses bcrypt with cost factor 12
      - Model has verify_password() method
    template_files:
      - src/models/user.py.j2
      - tests/test_user.py.j2
```

**Impact**: Don't reinvent common patterns. Community knowledge. Faster starts.

---

## Utility Assessment

### Current Utility

| Use Case | Readiness | Notes |
|----------|-----------|-------|
| Solo side projects | ✅ Ready | MVP scope is well-suited |
| Learning to code | ⚠️ Partial | Needs more explanation/teaching mode |
| Professional development | ❌ Not ready | Missing team features, robustness |
| Existing codebases | ❌ Not ready | No archaeology/onboarding |
| Enterprise use | ❌ Not ready | No auth, audit, compliance |

### Target Market Fit

**Strong fit for**:
- Solo developers with side project ideas
- Developers learning new domains
- Rapid prototyping
- Hackathons and time-boxed projects

**Weak fit for**:
- Teams (no collaboration)
- Existing large codebases (no archaeology)
- Highly regulated industries (no audit trail beyond git)
- Offline/air-gapped environments

### Competitive Position

| Competitor | Waypoints Advantage | Waypoints Disadvantage |
|------------|---------------------|------------------------|
| GitHub Copilot | End-to-end journey, not just code completion | Less mature, smaller ecosystem |
| Cursor | Structured planning, audit trail | Less polished editor experience |
| Replit Agent | Local-first, privacy | No cloud deployment |
| v0.dev | Full stack, not just UI | Limited to greenfield |

---

## Risk Analysis

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Claude API changes | Medium | High | Abstract LLM layer, support multiple providers |
| Agent SDK instability | Medium | High | Pin versions, maintain fallback |
| Textual breaking changes | Low | Medium | Pin version, test on upgrades |
| JSONL corruption | Low | High | Add checksums, backup on write |

### Product Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Users don't trust AI plans | High | High | Better explanation, easy editing |
| Execution failures frustrate | High | High | Intervention UI, graceful degradation |
| Cost surprises | Medium | High | Budget controls, estimates |
| Scope creep in waypoints | Medium | Medium | Size limits, complexity warnings |

### Market Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Big player enters market | High | Medium | Focus on niche, build community |
| AI capabilities plateau | Low | High | Design for current capabilities |
| Developer skepticism of AI | Medium | Medium | Transparency, control, audit trails |

---

## Actionable Next Steps

### Immediate (This Week)

1. **Add cost tracking to `ChatClient`**
   - Record tokens and cost per call
   - Display running total in status bar
   - File: `llm/client.py`, `tui/widgets/header.py`

2. **Implement basic intervention UI**
   - Modal when executor hits MAX_ITERATIONS
   - Options: Retry, Skip, Edit waypoint, Abort
   - File: `fly/executor.py`, `tui/screens/fly.py`

3. **Extract prompts to YAML files**
   - Move hardcoded prompts to `.waypoints/prompts/`
   - Add simple loader
   - Files: `llm/prompts.py`, `screens/*.py`

### Short-term (This Month)

4. **Implement state machine**
   - Define `JourneyState` enum
   - Add transition validation
   - Persist state to `project.json`
   - Files: `models/journey.py`, `models/project.py`

5. **Add multi-project dashboard**
   - List all projects in `.waypoints/projects/`
   - Show phase, last updated, progress
   - Quick resume from any project
   - File: `tui/screens/dashboard.py`

6. **Improve waypoint editing**
   - Inline title/objective editing
   - Drag-and-drop reordering (or keyboard shortcuts)
   - Visual dependency lines
   - File: `tui/widgets/flight_plan.py`

### Medium-term (This Quarter)

7. **Build orchestration layer**
   - Extract business logic from TUI
   - Create `JourneyCoordinator`
   - Enable headless/API mode
   - Directory: `orchestration/`

8. **Add local model support**
   - Ollama integration for offline use
   - Model selection in settings
   - Graceful fallback chain
   - File: `llm/providers/`

9. **Implement learning system foundation**
   - Log all executions with outcomes
   - Pattern extraction (success/failure)
   - Basic similar-waypoint lookup
   - Directory: `learning/`

### Long-term (This Year)

10. **Code archaeology**
11. **Waypoint marketplace**
12. **Team collaboration**
13. **Voice interface**
14. **Enterprise features**

---

## Conclusion

Waypoints has a compelling vision and solid architectural foundation. The core insight—that AI should orchestrate the development journey while humans provide vision—is sound and timely.

**Key strengths**: Clean architecture, crash-safe persistence, trust-but-verify pattern, hierarchical planning.

**Critical gaps**: Error recovery, cost visibility, limited editing, single-project focus.

**Path to 100X**: Learning system + code archaeology + waypoint marketplace would create a flywheel where Waypoints gets smarter with every project, making it indispensable for developers.

The MVP is ready for validation with early adopters. Focus immediate efforts on intervention handling and cost tracking to prevent user frustration, then build toward the learning system that will differentiate Waypoints from competitors.

---

*This assessment is based on codebase analysis as of commit 8a1274c. Recommendations should be validated against current product priorities and resource constraints.*
