# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## First Things First

BEFORE ANYTHING ELSE: run `bd onboard` and follow the instructions.

## Build & Test Commands

```bash
# Install/sync dependencies
uv sync

# Run tests with coverage
uv run pytest

# Run a single test file
uv run pytest tests/test_waypoints.py

# Run a specific test
uv run pytest tests/test_waypoints.py::test_hello_default

# Linting and formatting
uv run ruff check .          # Lint
uv run ruff check . --fix    # Lint with auto-fix
uv run black .               # Format
uv run black --check .       # Check formatting

# Type checking
uv run mypy src/

# Run the application
uv run waypoints
```

## Project Structure

```
src/waypoints/     # Main package (src layout)
tests/             # Test files (pytest auto-discovers)
```

## Code Style

- Line length: 88 (black/ruff)
- Ruff rules: E, F, I, N, W
- Mypy: strict mode enabled
- Python: 3.14+

## Issue Tracking

This project uses **bd** (beads) for issue tracking. See AGENTS.md for workflow details.
