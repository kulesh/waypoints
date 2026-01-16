# Waypoints

```
                                        ✈
                                      ╱
    ◇───────◇───────◇───────◆───────╯
   SPARK   SHAPE   CHART    FLY
```

**An AI-native software development environment that turns ideas into working software.**

Waypoints transforms software development from a task-management exercise into a guided journey. Like an aircraft's flight management system, Waypoints takes your destination (a product idea), collaborates with you to chart waypoints, then engages autopilot for the journey—with you always able to take control.

> **Status**: Work in progress. All four phases (SPARK, SHAPE, CHART, FLY) are functional.

## Vision: Generative Specification as Distribution

Waypoints is an implementation of [Generative Software](https://github.com/kulesh/generative-software)—a paradigm where the **generative specification (genspec)** becomes the distribution medium, not code.

Think of it like JIT compilation: just as JIT transforms bytecode into machine code at runtime, generative software transforms *specifications* into working software on demand. You don't ship the compiled binary—you ship the spec.

A genspec captures:
- **What** the software should do (product spec)
- **How** to build it (waypoints with acceptance criteria)
- **Context** for AI execution (enough detail for reliable regeneration)

This makes code *disposable*. Lost your codebase? Regenerate it from the genspec. Want to port to a new framework? Regenerate with different constraints. The specification becomes the source of truth, not the code.

## The Journey

Waypoints guides you through four phases:

```
┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
│  SPARK   │ ───► │  SHAPE   │ ───► │  CHART   │ ───► │   FLY    │
│          │      │          │      │          │      │          │
│ Enter    │      │ Refine   │      │ Generate │      │ Execute  │
│ idea     │      │ via Q&A  │      │ waypoints│      │ waypoints│
└──────────┘      └──────────┘      └──────────┘      └──────────┘
```

1. **SPARK**: Enter your idea in natural language
2. **SHAPE**: Refine through Socratic Q&A dialogue → produces Idea Brief & Product Spec
3. **CHART**: AI generates waypoints (development tasks) from your spec
4. **FLY**: Autopilot executes each waypoint using AI agents (writes tests, implements code, commits)

The output of phases 1-3 is the **generative specification**—a complete, reproducible blueprint for your software.

## Features

- **Conversational ideation**: Talk through your idea with AI that asks clarifying questions
- **Live brief generation**: Watch your idea crystallize into a structured document
- **Automatic waypoint planning**: AI decomposes your product spec into executable tasks
- **Waypoint management**: Add, edit, break down, reprioritize, or delete waypoints with AI assistance
- **Agentic execution**: AI implements each waypoint with stack-aware validation (linting, tests, type checking)
- **Inline AI editing**: Leave `@waypoints:` instructions in documents for AI to process
- **Genspec export**: Export your complete specification for sharing or regeneration
- **Document versioning**: All document changes create new timestamped versions
- **Crash-safe persistence**: Resume exactly where you left off
- **Terminal-native TUI**: Fast, keyboard-driven interface built with [Textual](https://textual.textualize.io/)

## Installation

Waypoints requires Python 3.14+ and uses [uv](https://github.com/astral-sh/uv) for dependency management.

```bash
# Clone the repository
git clone https://github.com/kulesh/waypoints.git
cd waypoints

# Install dependencies
uv sync

# Run Waypoints
uv run waypoints
```

## Quick Start

1. Launch Waypoints:
   ```bash
   uv run waypoints
   ```

2. Enter your idea when prompted (e.g., "A habit tracking app with streaks")

3. Answer clarifying questions to shape your idea

4. Review and edit the generated product spec

5. Approve the generated waypoints

6. Press `r` to start autopilot execution

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `?` | Show help |
| `j/k` | Navigate up/down |
| `Enter` | Select/confirm |
| `Escape` | Go back |
| `Ctrl+Q` | Quit |

**Document Review (SHAPE phase):**

| Key | Action |
|-----|--------|
| `Ctrl+E` | Toggle inline edit mode |
| `Ctrl+R` | Process `@waypoints:` mentions |
| `Ctrl+S` | Save document |
| `e` | Open in external editor |
| `Ctrl+Enter` | Proceed to next phase |

**Waypoint Planning (CHART phase):**

| Key | Action |
|-----|--------|
| `a` | Add new waypoint (AI-assisted) |
| `e` | Edit selected waypoint |
| `b` | Break down into sub-waypoints |
| `d` | Delete waypoint |
| `R` | AI-assisted reprioritization |
| `Ctrl+Enter` | Proceed to FLY phase |

**Execution (FLY phase):**

| Key | Action |
|-----|--------|
| `r` | Start/resume execution |
| `p` | Pause execution |

## Project Structure

```
src/waypoints/
├── tui/                 # Terminal UI (Textual app)
│   ├── screens/         # Phase screens (SPARK, SHAPE, CHART, FLY)
│   └── widgets/         # Reusable UI components
├── models/              # Data models (Project, Waypoint, FlightPlan)
├── fly/                 # Execution engine with stack detection
├── genspec/             # Generative specification export/import
└── llm/                 # AI client (Claude Agent SDK)

docs/
├── idea.md              # Original idea document
├── product-spec.md      # Full product specification
└── testing-strategy.md  # AI system testing approach
```

## How It Works

### State Persistence

Each project creates a `.waypoints/` directory containing:
- `project.json` - Project metadata
- `dialogue.jsonl` - Conversation history
- `flight-plan.json` - Waypoint definitions and status
- `docs/` - Generated documents (idea brief, product spec)
- `sessions/` - Execution logs and history

### Waypoint Execution

In the FLY phase, for each waypoint the AI:
1. Analyzes the objective and acceptance criteria
2. Detects project stack (Python, TypeScript, Go, Rust, etc.)
3. Implements code to achieve the objective
4. Runs stack-appropriate validation (linting, tests, type checking)
5. Produces a checklist receipt as proof of work
6. Commits with waypoint reference

Waypoints use a state machine:
- `PENDING` → waiting for dependencies
- `IN_PROGRESS` → currently executing
- `FAILED` → execution failed (can retry)
- `COMPLETE` → successfully finished

### Inline AI Editing

During document review (Idea Brief or Product Spec), you can leave instructions for the AI:

```markdown
## Problem Statement

This section describes the problem.

@waypoints: please expand this section with specific user pain points
```

Press `Ctrl+R` to process all `@waypoints:` mentions. The AI:
1. Reads the full document for context
2. Updates each section based on its instruction
3. Marks mentions as resolved
4. Saves a new timestamped version of the document

## Development

```bash
# Install dev dependencies
uv sync

# Run tests
uv run pytest

# Linting and formatting
uv run ruff check .
uv run black .

# Type checking
uv run mypy src/
```

## Technology

- **[Textual](https://textual.textualize.io/)** - Modern TUI framework for Python
- **[Claude Agent SDK](https://docs.anthropic.com/en/docs/claude-agent-sdk)** - AI agent execution
- **[uv](https://github.com/astral-sh/uv)** - Fast Python package manager

## Related

- **[Generative Software](https://github.com/kulesh/generative-software)** - The paradigm behind Waypoints

## License

MIT

## Contributing

This project is in early development. Issues and PRs welcome.

---

*"Waypoints is inspired by how an autopilot flies an aircraft from origin to destination following waypoints programmed into the flight management system by the pilots."*
