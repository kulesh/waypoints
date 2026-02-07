#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "README.md" ]]; then
  echo "missing README.md"
  exit 1
fi

if [[ -f "pyproject.toml" ]]; then
  uv run pytest --maxfail=1 --disable-warnings >/dev/null
fi

