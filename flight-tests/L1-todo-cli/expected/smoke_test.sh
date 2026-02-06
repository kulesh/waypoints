#!/usr/bin/env bash
set -euo pipefail

uv sync --quiet
uv run python -m todo_cli --help >/dev/null
