#!/usr/bin/env bash
set -euo pipefail

uv sync --quiet
output=$(uv run python -m hello_world)
echo "$output" | grep -q "Hello, World"
