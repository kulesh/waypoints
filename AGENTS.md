# Agent Instructions

This document provides guidance for AI coding agents (like Claude Code) working on this codebase.

## Issue Tracking

This project uses **[bd](https://github.com/kulesh/beads)** (beads) for issue tracking. Run `bd onboard` to get started.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

### Creating Issues

```bash
bd create --title="Fix authentication bug" --type=bug --priority=2
bd create --title="Add dark mode" --type=feature --priority=3
```

Priority: 0=critical, 1=high, 2=medium, 3=low, 4=backlog

### Managing Dependencies

```bash
bd dep add <issue> <depends-on>  # Issue depends on another
bd blocked                        # Show blocked issues
```

## Session Workflow

### Starting Work

1. Check for available work: `bd ready`
2. Review issue details: `bd show <id>`
3. Claim the work: `bd update <id> --status in_progress`
4. Start coding

### Landing the Plane (Session Completion)

**When ending a work session**, complete ALL steps below. Work is NOT complete until `git push` succeeds.

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed):
   ```bash
   uv run pytest           # Tests
   uv run ruff check .     # Linting
   uv run mypy src/        # Type checking
   ```
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Verify** - All changes committed AND pushed

### Critical Rules

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

## Code Standards

See [CLAUDE.md](./CLAUDE.md) for build commands, code style, and project structure.

### Key Points

- Python 3.14+
- Use `uv run` prefix for all commands
- Black formatting (line length 88)
- Ruff linting
- Mypy strict mode
- All tests must pass before committing
