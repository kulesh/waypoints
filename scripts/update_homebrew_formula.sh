#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT_PATH="$ROOT_DIR/pyproject.toml"
FORMULA_PATH="$ROOT_DIR/Formula/waypoints.rb"

if [[ ! -f "$PYPROJECT_PATH" ]]; then
  echo "error: pyproject.toml not found at $PYPROJECT_PATH" >&2
  exit 1
fi

if [[ ! -f "$FORMULA_PATH" ]]; then
  echo "error: formula not found at $FORMULA_PATH" >&2
  echo "Create Formula/waypoints.rb before running this script." >&2
  exit 1
fi

VERSION="$(
  python - <<'PY' "$PYPROJECT_PATH"
import pathlib
import tomllib
import sys

pyproject_path = pathlib.Path(sys.argv[1])
data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
print(data["project"]["version"])
PY
)"
TAG="v${VERSION}"
URL="https://github.com/kulesh/waypoints/archive/refs/tags/${TAG}.tar.gz"

echo "Generating Homebrew formula for ${TAG}"
echo "Downloading ${URL}"

TMP_ARCHIVE="$(mktemp)"
trap 'rm -f "$TMP_ARCHIVE"' EXIT

curl -LfsS "$URL" -o "$TMP_ARCHIVE"
SHA256="$(shasum -a 256 "$TMP_ARCHIVE" | awk '{print $1}')"

python - <<'PY' "$FORMULA_PATH" "$URL" "$VERSION" "$SHA256"
from __future__ import annotations

import pathlib
import re
import sys

formula_path = pathlib.Path(sys.argv[1])
url = sys.argv[2]
version = sys.argv[3]
sha256 = sys.argv[4]

content = formula_path.read_text(encoding="utf-8")
updated = re.sub(r'^\s*url ".*"$', f'  url "{url}"', content, count=1, flags=re.M)
updated = re.sub(
    r'^\s*version ".*"$',
    f'  version "{version}"',
    updated,
    count=1,
    flags=re.M,
)
updated = re.sub(
    r'^\s*sha256 ".*"$',
    f'  sha256 "{sha256}"',
    updated,
    count=1,
    flags=re.M,
)

formula_path.write_text(updated, encoding="utf-8")
PY

echo "Wrote ${FORMULA_PATH}"
echo "sha256=${SHA256}"
