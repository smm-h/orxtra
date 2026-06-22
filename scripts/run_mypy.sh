#!/bin/bash
set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
for mod in protocols secrets write-safety transport agent tool verify trace notepad session scheduler overseer services cli mcp; do
  echo "=== $mod ==="
  cd "$REPO_ROOT/$mod"
  MYPYPATH=src uv run --with mypy --with 'pydantic[mypy]' python -m mypy --strict --explicit-package-bases src/orxtra/*/
  cd "$REPO_ROOT"
done
echo "All modules passed mypy --strict"
