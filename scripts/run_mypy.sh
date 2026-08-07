#!/bin/bash
# Run mypy --strict on every sub-project. Reports all failures
# and exits non-zero if any module has errors.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
failed=0
for mod in protocols secrets write-safety transport agent tool verify trace notepad session compose scheduler dispatch overseer services auth identity api worker a2a a2ui agui cli mcp incoming notification; do
  echo "=== $mod ==="
  cd "$REPO_ROOT/$mod"
  # Exclude generated code from the strict run:
  # - strictspec validators (_gen_*.py): DO-NOT-EDIT, trip --strict (untyped
  #   dict, Optional-vs-required narrowing).
  # - pgdesign DDL package (_generated/): DO-NOT-EDIT, has known protocol type
  #   gaps (async-context-manager protocol declared async def; untyped
  #   __aexit__ params). Filed as pgdesign todo.
  # Generated code is validated by its generator plus runtime tests.
  if ! MYPYPATH=src uv run --with mypy --with 'pydantic[mypy]' python -m mypy --strict --explicit-package-bases --exclude '_gen_.*\.py$' --exclude '(^|/)_generated/' src/orxtra/*/; then
    failed=1
  fi
  cd "$REPO_ROOT"
done
if [ "$failed" -ne 0 ]; then
  echo "FAILED: one or more modules had mypy errors"
  exit 1
fi
echo "All modules passed mypy --strict"
