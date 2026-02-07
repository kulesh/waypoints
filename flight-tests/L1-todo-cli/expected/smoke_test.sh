#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d "src" ]]; then
  echo "missing src/ directory"
  exit 1
fi

if [[ ! -d "tests" ]]; then
  echo "missing tests/ directory"
  exit 1
fi

if [[ -f "pyproject.toml" ]]; then
  uv run pytest --maxfail=1 --disable-warnings >/dev/null
fi

