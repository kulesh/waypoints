#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
TARGET_DIR="${1:-${HOME}/flight-tests/waypoints-self-host}"

# Keep generated artifacts outside the development tree by default.
if [[ "${TARGET_DIR}" == "${REPO_ROOT}"* ]]; then
  echo "target directory must be outside repository: ${TARGET_DIR}" >&2
  exit 1
fi

mkdir -p "${TARGET_DIR}"

echo "Launching Waypoints self-host run"
echo "Repo root: ${REPO_ROOT}"
echo "Workdir: ${TARGET_DIR}"

exec uv run --directory "${REPO_ROOT}" waypoints --workdir "${TARGET_DIR}"

