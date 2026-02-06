#!/usr/bin/env bash
set -euo pipefail

uv sync --quiet
uv run python -m todo_api --help >/dev/null
