#!/bin/bash
# Run mypy --strict on every sub-project. Reports all failures
# and exits non-zero if any module has errors.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
failed=0
for mod in protocols secrets write-safety transport agent tool verify trace notepad session compose scheduler dispatch overseer services auth api worker a2a a2ui agui cli mcp incoming; do
  echo "=== $mod ==="
  cd "$REPO_ROOT/$mod"
  if ! MYPYPATH=src uv run --with mypy --with 'pydantic[mypy]' python -m mypy --strict --explicit-package-bases src/orxtra/*/; then
    failed=1
  fi
  cd "$REPO_ROOT"
done
if [ "$failed" -ne 0 ]; then
  echo "FAILED: one or more modules had mypy errors"
  exit 1
fi
echo "All modules passed mypy --strict"
