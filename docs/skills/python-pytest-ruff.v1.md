# python-pytest-ruff v1

## Applicability

Use when Python project markers exist (`pyproject.toml` or requirements files).

## Preferred Commands

1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run mypy src/`
4. `uv run pytest`

## Test Strategy

1. write or update focused unit tests first
2. run targeted test files during iteration
3. run full suite before completion claim

## Anti-Patterns

1. skipping lints and formatting
2. shipping without type checks in strict modules
3. broad refactors without behavioral tests
