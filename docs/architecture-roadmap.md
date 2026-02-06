# Waypoints Architecture Roadmap

**Consolidated from**: Claude Review (2026-01-08), Codex Review (2026-01-08)
**Goal**: Reliability, Robustness, Observability
**Scope**: Architectural changes only (not features)

---

## Priority Matrix

| Priority | Theme | Impact | Effort |
|----------|-------|--------|--------|
| **P0** | State Machine | Prevents invalid states, enables recovery | Medium |
| **P0** | Intervention Protocol | Prevents lost work, builds trust | Medium |
| **P0** | Metrics & Cost Tracking | Budget control, optimization | Low |
| **P1** | Schema Versioning | Future-proofs persistence | Low |
| **P1** | LLM Output Validation | Prevents silent failures | Medium |
| **P1** | Centralized Paths | Reduces config bugs | Low |
| **P2** | Orchestration Layer ✓ | Enables testing, headless mode | High |
| **P2** | Execution Protocol | Deterministic FLY phase | High |

---

## P0: Critical (Do First)

### 1. Journey State Machine

**Problem**: State is implicit across screens. No validation of transitions. Crash during transition leaves undefined state.

**Solution**: Explicit state enum with transition table.

```python
# models/journey.py

class JourneyState(Enum):
    # SPARK
    SPARK_IDLE = "spark:idle"
    SPARK_ENTERING = "spark:entering"

    # SHAPE
    SHAPE_QA = "shape:qa"
    SHAPE_BRIEF_GENERATING = "shape:brief:generating"
    SHAPE_BRIEF_REVIEW = "shape:brief:review"
    SHAPE_SPEC_GENERATING = "shape:spec:generating"
    SHAPE_SPEC_REVIEW = "shape:spec:review"

    # CHART
    CHART_GENERATING = "chart:generating"
    CHART_REVIEW = "chart:review"

    # FLY
    FLY_READY = "fly:ready"
    FLY_EXECUTING = "fly:executing"
    FLY_PAUSED = "fly:paused"
    FLY_INTERVENTION = "fly:intervention"

    # LAND
    LANDED = "landed"

VALID_TRANSITIONS: dict[JourneyState, set[JourneyState]] = {
    JourneyState.SPARK_IDLE: {JourneyState.SPARK_ENTERING},
    JourneyState.SPARK_ENTERING: {JourneyState.SHAPE_QA},
    JourneyState.SHAPE_QA: {JourneyState.SHAPE_BRIEF_GENERATING},
    JourneyState.SHAPE_BRIEF_GENERATING: {JourneyState.SHAPE_BRIEF_REVIEW},
    JourneyState.SHAPE_BRIEF_REVIEW: {
        JourneyState.SHAPE_BRIEF_GENERATING,  # Regenerate
        JourneyState.SHAPE_SPEC_GENERATING,
    },
    JourneyState.SHAPE_SPEC_GENERATING: {JourneyState.SHAPE_SPEC_REVIEW},
    JourneyState.SHAPE_SPEC_REVIEW: {
        JourneyState.SHAPE_SPEC_GENERATING,  # Regenerate
        JourneyState.CHART_GENERATING,
    },
    JourneyState.CHART_GENERATING: {JourneyState.CHART_REVIEW},
    JourneyState.CHART_REVIEW: {
        JourneyState.CHART_GENERATING,  # Regenerate
        JourneyState.FLY_READY,
    },
    JourneyState.FLY_READY: {JourneyState.FLY_EXECUTING},
    JourneyState.FLY_EXECUTING: {
        JourneyState.FLY_PAUSED,
        JourneyState.FLY_INTERVENTION,
        JourneyState.LANDED,
    },
    JourneyState.FLY_PAUSED: {JourneyState.FLY_EXECUTING, JourneyState.FLY_READY},
    JourneyState.FLY_INTERVENTION: {
        JourneyState.FLY_EXECUTING,  # Retry
        JourneyState.FLY_PAUSED,     # Skip waypoint
        JourneyState.CHART_REVIEW,   # Edit plan
    },
    JourneyState.LANDED: set(),  # Terminal state
}

@dataclass
class Journey:
    state: JourneyState
    project_slug: str
    updated_at: datetime

    def can_transition(self, target: JourneyState) -> bool:
        return target in VALID_TRANSITIONS.get(self.state, set())

    def transition(self, target: JourneyState) -> "Journey":
        if not self.can_transition(target):
            raise InvalidTransition(self.state, target)
        return Journey(
            state=target,
            project_slug=self.project_slug,
            updated_at=datetime.now(UTC),
        )

    def save(self) -> None:
        """Persist to project.json atomically."""
        ...

    @classmethod
    def load(cls, project_slug: str) -> "Journey | None":
        """Load from project.json, validate state."""
        ...
```

**Files to modify**:
- Create `models/journey.py`
- Update `models/project.py` to include journey state
- Update `tui/app.py` to validate transitions before `push_screen()`
- Update each screen to call `journey.transition()` on phase change

**Acceptance criteria**:
- [ ] `JourneyState` enum covers all phases and sub-states
- [ ] Transitions validated before screen switches
- [ ] State persisted to `project.json` on every transition
- [ ] App startup validates and recovers from invalid states
- [ ] Tests cover all valid transitions and reject invalid ones

---

### 2. Intervention Protocol

**Problem**: When `WaypointExecutor` hits MAX_ITERATIONS or fails, it just stops. No recovery path. User loses context.

**Solution**: Structured intervention with user choice.

```python
# fly/intervention.py

class InterventionType(Enum):
    ITERATION_LIMIT = "iteration_limit"
    TEST_FAILURE = "test_failure"
    LINT_ERROR = "lint_error"
    TYPE_ERROR = "type_error"
    PARSE_ERROR = "parse_error"
    USER_REQUESTED = "user_requested"

class InterventionAction(Enum):
    RETRY = "retry"           # Try waypoint again (maybe with more iterations)
    SKIP = "skip"             # Mark waypoint skipped, continue to next
    EDIT = "edit"             # Open waypoint editor, then retry
    ROLLBACK = "rollback"     # Rollback to last safe tag
    ABORT = "abort"           # Stop execution entirely

@dataclass
class Intervention:
    type: InterventionType
    waypoint: Waypoint
    iteration: int
    error_summary: str
    suggested_action: InterventionAction
    context: dict[str, Any]  # Logs, last output, etc.

@dataclass
class InterventionResult:
    action: InterventionAction
    modified_waypoint: Waypoint | None  # If EDIT
    additional_iterations: int          # If RETRY
```

**TUI Component**:

```python
# tui/screens/intervention.py

class InterventionModal(ModalScreen[InterventionResult]):
    """Modal shown when execution needs human intervention."""

    def compose(self) -> ComposeResult:
        yield Container(
            Static(f"⚠️ Intervention Required: {self.intervention.type.value}"),
            Static(f"Waypoint: {self.intervention.waypoint.title}"),
            Static(f"Iteration: {self.intervention.iteration}/10"),
            Markdown(self.intervention.error_summary),
            Horizontal(
                Button("Retry (+5 iterations)", id="retry"),
                Button("Skip Waypoint", id="skip"),
                Button("Edit & Retry", id="edit"),
                Button("Rollback", id="rollback"),
                Button("Abort", id="abort"),
            ),
        )
```

**Files to modify**:
- Create `fly/intervention.py`
- Create `tui/screens/intervention.py`
- Update `fly/executor.py` to raise `InterventionNeeded` instead of returning
- Update `tui/screens/fly.py` to catch and show `InterventionModal`
- Update `models/journey.py` to handle `FLY_INTERVENTION` state

**Acceptance criteria**:
- [ ] Intervention modal appears on MAX_ITERATIONS
- [ ] Intervention modal appears on test/lint/type failures
- [ ] User can retry with additional iterations
- [ ] User can skip waypoint (marked as SKIPPED status)
- [ ] User can edit waypoint and retry
- [ ] User can rollback to last safe tag
- [ ] User can abort execution
- [ ] Journey state transitions to FLY_INTERVENTION and back

---

### 3. Metrics & Cost Tracking

**Problem**: No visibility into token usage, cost, or execution performance. Users can't budget or optimize.

**Solution**: Per-call metrics with aggregation.

```python
# llm/metrics.py

@dataclass
class LLMCall:
    call_id: str
    prompt_id: str              # Which prompt was used
    phase: str                  # spark, shape, chart, fly
    waypoint_id: str | None     # If during FLY
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    model: str
    timestamp: datetime
    success: bool
    error: str | None

class MetricsCollector:
    def __init__(self, project_path: Path):
        self.path = project_path / "metrics.jsonl"
        self._calls: list[LLMCall] = []
        self._load()

    def record(self, call: LLMCall) -> None:
        self._calls.append(call)
        self._append(call)

    def _append(self, call: LLMCall) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(call.to_dict()) + "\n")

    # Aggregations
    @property
    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self._calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self._calls)

    def cost_by_phase(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for call in self._calls:
            result[call.phase] = result.get(call.phase, 0) + call.cost_usd
        return result

    def cost_by_waypoint(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for call in self._calls:
            if call.waypoint_id:
                result[call.waypoint_id] = result.get(call.waypoint_id, 0) + call.cost_usd
        return result

    def summary(self) -> dict[str, Any]:
        return {
            "total_calls": len(self._calls),
            "total_cost_usd": self.total_cost,
            "total_tokens": self.total_tokens,
            "cost_by_phase": self.cost_by_phase(),
            "avg_latency_ms": mean(c.latency_ms for c in self._calls) if self._calls else 0,
            "success_rate": sum(1 for c in self._calls if c.success) / len(self._calls) if self._calls else 0,
        }

# Optional budget control
@dataclass
class Budget:
    max_usd: float | None = None
    max_tokens: int | None = None

    def check(self, collector: MetricsCollector) -> None:
        if self.max_usd and collector.total_cost > self.max_usd:
            raise BudgetExceeded("cost", collector.total_cost, self.max_usd)
        if self.max_tokens and collector.total_tokens > self.max_tokens:
            raise BudgetExceeded("tokens", collector.total_tokens, self.max_tokens)
```

**TUI Integration**:

```python
# tui/widgets/metrics.py

class MetricsSummary(Static):
    """Shows running cost and token count in status bar."""

    def __init__(self, collector: MetricsCollector):
        super().__init__()
        self.collector = collector

    def render(self) -> str:
        return f"${self.collector.total_cost:.2f} | {self.collector.total_tokens:,} tokens"
```

**Files to modify**:
- Create `llm/metrics.py`
- Create `tui/widgets/metrics.py`
- Update `llm/client.py` to record metrics on every call
- Update `tui/widgets/header.py` to show `MetricsSummary`
- Update `tui/screens/fly.py` to show per-waypoint cost

**Acceptance criteria**:
- [ ] Every LLM call recorded to `metrics.jsonl`
- [ ] Status bar shows running total cost
- [ ] FLY screen shows per-waypoint cost
- [ ] `metrics.summary()` returns aggregated stats
- [ ] Optional budget enforcement with clear error

---

## P1: Important (Do Soon)

### 4. Schema Versioning for JSONL

**Problem**: No migration path for JSONL format changes. Breaking change = lost data.

**Solution**: Version header + validators + migrators.

```python
# models/schema.py

CURRENT_VERSIONS = {
    "session": "1.0",
    "flight_plan": "1.0",
    "execution_log": "1.0",
    "metrics": "1.0",
}

@dataclass
class SchemaHeader:
    schema_type: str
    schema_version: str
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "_schema": self.schema_type,
            "_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
        }

def validate_header(line: str, expected_type: str) -> SchemaHeader:
    """Validate first line of JSONL file."""
    data = json.loads(line)
    if "_schema" not in data:
        # Legacy file without schema - assume v0
        return SchemaHeader(expected_type, "0.0", datetime.now(UTC))
    return SchemaHeader(
        schema_type=data["_schema"],
        schema_version=data["_version"],
        created_at=datetime.fromisoformat(data["created_at"]),
    )

def migrate_if_needed(path: Path, schema_type: str) -> None:
    """Migrate file to current schema version if needed."""
    with open(path) as f:
        header = validate_header(f.readline(), schema_type)

    current = CURRENT_VERSIONS[schema_type]
    if header.schema_version == current:
        return  # Already current

    migrator = MIGRATORS.get((schema_type, header.schema_version, current))
    if migrator:
        migrator(path)
    else:
        raise MigrationNotFound(schema_type, header.schema_version, current)
```

**Files to modify**:
- Create `models/schema.py`
- Update `models/session.py` to write/validate header
- Update `models/flight_plan.py` to write/validate header
- Update `fly/execution_log.py` to write/validate header
- Add `migrate_if_needed()` calls on load

**Acceptance criteria**:
- [ ] All JSONL files have versioned header
- [ ] Loading validates schema version
- [ ] Migration framework in place (even if no migrations yet)
- [ ] Legacy files (no header) treated as v0 and migrated

---

### 5. LLM Output Validation

**Problem**: Waypoint JSON parsing is fragile. Invalid output causes silent failures or crashes.

**Solution**: JSON schema validation + repair prompting.

```python
# llm/validation.py

WAYPOINT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["id", "title", "objective", "acceptance_criteria"],
        "properties": {
            "id": {"type": "string", "pattern": "^WP-[0-9]+[a-z]?$"},
            "title": {"type": "string", "minLength": 3},
            "objective": {"type": "string", "minLength": 10},
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "parent_id": {"type": ["string", "null"]},
            "dependencies": {"type": "array", "items": {"type": "string"}},
        },
    },
}

def validate_waypoints(json_str: str) -> tuple[list[dict], list[str]]:
    """Validate waypoint JSON, return (waypoints, errors)."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return [], [f"Invalid JSON: {e}"]

    errors = []
    # Schema validation
    try:
        jsonschema.validate(data, WAYPOINT_SCHEMA)
    except jsonschema.ValidationError as e:
        errors.append(f"Schema error: {e.message}")

    # Semantic validation
    ids = [wp["id"] for wp in data]
    if len(ids) != len(set(ids)):
        errors.append("Duplicate waypoint IDs")

    for wp in data:
        if wp.get("parent_id") and wp["parent_id"] not in ids:
            errors.append(f"{wp['id']}: parent_id references non-existent {wp['parent_id']}")
        for dep in wp.get("dependencies", []):
            if dep not in ids:
                errors.append(f"{wp['id']}: dependency references non-existent {dep}")

    # Cycle detection
    cycles = detect_cycles(data)
    if cycles:
        errors.append(f"Dependency cycles: {cycles}")

    return data if not errors else [], errors

REPAIR_PROMPT = """
The waypoint JSON you generated has errors:
{errors}

Original JSON:
{json_str}

Please fix the errors and output valid JSON only.
"""

async def parse_with_repair(
    client: ChatClient,
    json_str: str,
    max_repairs: int = 2,
) -> list[dict]:
    """Parse waypoints, prompting for repairs if needed."""
    for attempt in range(max_repairs + 1):
        waypoints, errors = validate_waypoints(json_str)
        if not errors:
            return waypoints
        if attempt == max_repairs:
            raise ValidationFailed(errors)

        # Ask model to repair
        repair_response = await client.query(
            REPAIR_PROMPT.format(errors=errors, json_str=json_str)
        )
        json_str = extract_json(repair_response)

    return waypoints
```

**Files to modify**:
- Create `llm/validation.py`
- Update `tui/screens/chart.py` to use `parse_with_repair()`
- Add `jsonschema` to dependencies

**Acceptance criteria**:
- [ ] Waypoint JSON validated against schema
- [ ] Semantic validation (IDs, parent refs, dependencies, cycles)
- [ ] Model prompted to repair invalid output (max 2 attempts)
- [ ] Clear error if repair fails

---

### 6. Centralized Path Management

**Problem**: Paths scattered across modules. Inconsistent handling of workspace vs global config.

**Solution**: Single `Paths` module.

```python
# config/paths.py

@dataclass
class WaypointsPaths:
    """Centralized path management."""

    workspace: Path  # Current working directory

    @property
    def workspace_config(self) -> Path:
        return self.workspace / ".waypoints"

    @property
    def projects_dir(self) -> Path:
        return self.workspace_config / "projects"

    def project_dir(self, slug: str) -> Path:
        return self.projects_dir / slug

    def project_file(self, slug: str) -> Path:
        return self.project_dir(slug) / "project.json"

    def sessions_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / "sessions"

    def docs_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / "docs"

    def flight_plan(self, slug: str) -> Path:
        return self.project_dir(slug) / "flight-plan.jsonl"

    def metrics(self, slug: str) -> Path:
        return self.project_dir(slug) / "metrics.jsonl"

    def receipts_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / "receipts"

    def checklist(self, slug: str) -> Path:
        return self.project_dir(slug) / "checklist.yaml"

    # Global paths
    @property
    def global_config(self) -> Path:
        return Path.home() / ".waypoints"

    @property
    def global_settings(self) -> Path:
        return self.global_config / "settings.json"

    @property
    def global_git_config(self) -> Path:
        return self.global_config / "git-config.json"

    # Debug logs
    @property
    def debug_log(self) -> Path:
        return self.workspace_config / "debug.log"

    # Resolution with fallbacks
    def git_config(self, slug: str | None = None) -> Path:
        """Resolve git config: project > workspace > global."""
        if slug:
            project_config = self.project_dir(slug) / "git-config.json"
            if project_config.exists():
                return project_config

        workspace_config = self.workspace_config / "git-config.json"
        if workspace_config.exists():
            return workspace_config

        return self.global_git_config

    def ensure_dirs(self, slug: str) -> None:
        """Create all directories for a project."""
        self.project_dir(slug).mkdir(parents=True, exist_ok=True)
        self.sessions_dir(slug).mkdir(exist_ok=True)
        self.docs_dir(slug).mkdir(exist_ok=True)
        self.receipts_dir(slug).mkdir(exist_ok=True)

# Singleton
_paths: WaypointsPaths | None = None

def get_paths() -> WaypointsPaths:
    global _paths
    if _paths is None:
        _paths = WaypointsPaths(workspace=Path.cwd())
    return _paths
```

**Files to modify**:
- Create `config/paths.py`
- Update `models/project.py` to use `get_paths()`
- Update `models/session.py` to use `get_paths()`
- Update `git/config.py` to use `get_paths()`
- Update `config.py` to use `get_paths()`

**Acceptance criteria**:
- [ ] All path construction goes through `WaypointsPaths`
- [ ] Config resolution follows project > workspace > global
- [ ] No hardcoded `.waypoints/` strings outside `paths.py`

---

## P2: Valuable (Do When Ready)

### 7. Orchestration Layer ✓ COMPLETE

**Problem**: TUI screens contain business logic. Can't test without UI. Can't run headless.

**Solution**: Extract coordination to `JourneyCoordinator`.

**Status**: Implemented. `JourneyCoordinator` delegates to phase-specific classes
(`FlyPhase`, `ChartPhase`, `ShapePhase`). FlyScreen no longer imports
`WaypointExecutor`, `GitService`, or directly mutates waypoint status. All business
logic flows through the orchestration layer. Executor internals decomposed from a
single 715-line method into focused sub-methods sharing state via `_LoopState`.
Modal CSS duplication eliminated via `WaypointModalBase`.

```python
# orchestration/coordinator.py

class JourneyCoordinator:
    """Coordinates journey phases independent of UI."""

    def __init__(
        self,
        project: Project,
        journey: Journey,
        llm: ChatClient,
        git: GitService,
        metrics: MetricsCollector,
        prompts: PromptRegistry,
    ):
        self.project = project
        self.journey = journey
        self.llm = llm
        self.git = git
        self.metrics = metrics
        self.prompts = prompts

    # Phase operations
    async def start_shape_qa(self) -> AsyncIterator[str]:
        """Start Q&A dialogue, yield responses."""
        self.journey = self.journey.transition(JourneyState.SHAPE_QA)
        self.journey.save()
        ...

    async def submit_qa_response(self, user_input: str) -> AsyncIterator[str]:
        """Process user response, yield AI response."""
        ...

    async def generate_brief(self) -> AsyncIterator[str]:
        """Generate idea brief from dialogue."""
        self.journey = self.journey.transition(JourneyState.SHAPE_BRIEF_GENERATING)
        self.journey.save()
        ...

    async def generate_spec(self) -> AsyncIterator[str]:
        """Generate product spec from brief."""
        ...

    async def generate_waypoints(self) -> list[Waypoint]:
        """Generate flight plan from spec."""
        ...

    async def execute_waypoint(
        self,
        waypoint_id: str,
        on_progress: Callable[[ExecutionContext], None] | None = None,
    ) -> ExecutionResult | Intervention:
        """Execute single waypoint, return result or intervention."""
        ...

    async def handle_intervention(
        self,
        intervention: Intervention,
        action: InterventionAction,
        **kwargs,
    ) -> ExecutionResult:
        """Handle intervention action, continue execution."""
        ...
```

**Files to create**:
- `orchestration/__init__.py`
- `orchestration/coordinator.py`
- `orchestration/events.py` (event types for callbacks)

**Files to modify**:
- Update all `tui/screens/*.py` to use `JourneyCoordinator`
- Screens become thin wrappers that call coordinator and render results

**Acceptance criteria**:
- [ ] All business logic in `JourneyCoordinator`
- [ ] Screens only handle rendering and user input
- [ ] Coordinator can be instantiated and tested without TUI
- [ ] Headless mode possible (for CI/scripting)

---

### 8. Structured Execution Protocol

**Problem**: FLY executor relies on `<waypoint-complete>` marker. No structured protocol. Hard to verify quality.

**Solution**: Explicit execution stages with structured reports.

```python
# fly/protocol.py

class ExecutionStage(Enum):
    ANALYZE = "analyze"      # Read waypoint, understand context
    PLAN = "plan"            # Decide approach
    TEST = "test"            # Write tests first
    CODE = "code"            # Implement
    RUN = "run"              # Run tests
    FIX = "fix"              # Fix failures
    LINT = "lint"            # Run linters
    REPORT = "report"        # Generate structured report

@dataclass
class StageReport:
    stage: ExecutionStage
    success: bool
    output: str
    artifacts: list[str]     # Files created/modified
    next_stage: ExecutionStage | None

@dataclass
class ExecutionReport:
    waypoint_id: str
    stages: list[StageReport]
    final_status: Literal["complete", "failed", "intervention"]
    test_results: TestResults | None
    lint_results: LintResults | None
    files_changed: list[str]

    def to_receipt(self, checklist: Checklist) -> ChecklistReceipt:
        """Convert execution report to checklist receipt."""
        items = []
        for item in checklist.items:
            if "test" in item.lower():
                status = "passed" if self.test_results and self.test_results.passed else "failed"
                evidence = str(self.test_results) if self.test_results else ""
            elif "lint" in item.lower():
                status = "passed" if self.lint_results and self.lint_results.passed else "failed"
                evidence = str(self.lint_results) if self.lint_results else ""
            # ... other mappings
            items.append(ChecklistItem(item=item, status=status, evidence=evidence))

        return ChecklistReceipt(
            waypoint_id=self.waypoint_id,
            completed_at=datetime.now(UTC),
            checklist=items,
        )

# Updated executor prompt
EXECUTION_PROMPT = """
You are executing waypoint {waypoint_id}: {title}

Objective: {objective}

Acceptance Criteria:
{criteria}

Execute in stages. After each stage, output a structured report:

```json
{{
  "stage": "analyze|plan|test|code|run|fix|lint|report",
  "success": true|false,
  "output": "description of what was done",
  "artifacts": ["file1.py", "file2.py"],
  "next_stage": "next_stage_name" or null if done
}}
```

Proceed through stages until complete or intervention needed.
"""
```

**Files to modify**:
- Create `fly/protocol.py`
- Update `fly/executor.py` to use structured stages
- Update `fly/execution_log.py` to store stage reports
- Update receipt generation to use execution report

**Acceptance criteria**:
- [ ] Execution proceeds through defined stages
- [ ] Each stage produces structured JSON report
- [ ] Reports validate against schema
- [ ] Receipt generated from execution report
- [ ] Stage failures trigger intervention

---

## Implementation Order

```
Week 1-2: P0 Items (Critical)
├── 1. Journey State Machine
├── 2. Intervention Protocol
└── 3. Metrics & Cost Tracking

Week 3-4: P1 Items (Important)
├── 4. Schema Versioning
├── 5. LLM Output Validation
└── 6. Centralized Paths

Week 5-8: P2 Items (Valuable)
├── 7. Orchestration Layer
└── 8. Structured Execution Protocol
```

---

## Validation Checklist

After completing all items, the system should pass these checks:

### Reliability
- [ ] Invalid state transitions rejected with clear error
- [ ] Crash during any phase recovers to last valid state
- [ ] Failed waypoint triggers intervention, not silent stop
- [ ] LLM output validated and repaired before use

### Robustness
- [ ] Schema changes don't break existing projects
- [ ] Config resolution consistent across all components
- [ ] Budget limits enforced before costly operations
- [ ] Rollback to last safe tag always possible

### Observability
- [ ] Every LLM call logged with tokens, cost, latency
- [ ] Per-phase and per-waypoint cost visible
- [ ] Execution stages visible during FLY
- [ ] Metrics exportable for analysis

---

## Dependencies

### New Packages
- `jsonschema` - LLM output validation

### New Files
```
models/journey.py           # State machine
models/schema.py            # Schema versioning
config/paths.py             # Centralized paths
llm/metrics.py              # Cost tracking
llm/validation.py           # Output validation
fly/intervention.py         # Intervention types
fly/protocol.py             # Execution protocol
tui/screens/intervention.py # Intervention modal
tui/widgets/metrics.py      # Metrics display
orchestration/__init__.py   # Package
orchestration/coordinator.py # Business logic
orchestration/events.py     # Event types
```

---

## Beads Tracking

**Epic**: `waypoints-ecu` — Architecture Improvements: Reliability, Robustness, Observability

### Issue Inventory

| ID | Task | Priority | Status | Blocked By |
|----|------|----------|--------|------------|
| `waypoints-4kn` | Journey State Machine | P0 | Ready | — |
| `waypoints-4di` | Metrics & Cost Tracking | P0 | Ready | — |
| `waypoints-d0j` | Intervention Protocol | P0 | Blocked | `waypoints-4kn` |
| `waypoints-f0p` | Schema Versioning for JSONL | P1 | Ready | — |
| `waypoints-hpo` | LLM Output Validation | P1 | Ready | — |
| `waypoints-15q` | Centralized Path Management | P1 | Ready | — |
| `waypoints-vku` | Orchestration Layer | P2 | **Complete** | `waypoints-4kn` |
| `waypoints-zgf` | Structured Execution Protocol | P2 | Blocked | `waypoints-d0j`, `waypoints-vku` |

### Dependency Graph

```
                    ┌─────────────────────────────────────────────┐
                    │           READY TO START                     │
                    ├─────────────────────────────────────────────┤
                    │                                             │
  P0:               │  waypoints-4kn ──────┬──────────────────────┤
  State Machine     │  (State Machine)     │                      │
                    │                      │                      │
  P0:               │  waypoints-4di       │                      │
  Metrics           │  (Metrics)           │                      │
                    │                      │                      │
  P1:               │  waypoints-f0p       │                      │
  Schema            │  waypoints-hpo       │                      │
                    │  waypoints-15q       │                      │
                    │                      │                      │
                    └──────────────────────┼──────────────────────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    │           BLOCKED                           │
                    ├──────────────────────┼──────────────────────┤
                    │                      │                      │
  P0:               │                      ▼                      │
  Intervention      │              waypoints-d0j ─────────────────┤
                    │              (Intervention)                 │
                    │                      │                      │
  P2:               │                      │     waypoints-vku ◄──┤
  Orchestration     │                      │     (Orchestration)  │
                    │                      │           │          │
                    │                      ▼           ▼          │
  P2:               │              waypoints-zgf                  │
  Exec Protocol     │              (Execution Protocol)           │
                    │                      │                      │
                    └──────────────────────┼──────────────────────┘
                                           │
                                           ▼
                                   waypoints-ecu
                                   (EPIC COMPLETE)
```

### Quick Commands

```bash
# View epic status
bd show waypoints-ecu

# See what's ready to work on
bd ready

# Start working on State Machine
bd update waypoints-4kn --status=in_progress

# Complete a task
bd close waypoints-4kn

# Sync after work
bd sync
```

---

*This roadmap consolidates recommendations from both architectural reviews. Items are prioritized by impact on reliability, robustness, and observability. Implementation order balances dependencies and value delivery.*
