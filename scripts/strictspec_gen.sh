#!/usr/bin/env bash
# Regenerate and freshness-check every strictspec validator in the workspace.
#
# Each sub-project that owns spec documents carries a strictspec.toml manifest
# (schemas/*.schema.toml -> src/orxtra/<pkg>/_gen_*.py). The generated validators
# are committed (chmod 444) and imported at each loader's document boundary. Run
# this after editing any schema, and in CI, to keep generated code in lockstep
# with its schema (strictspec check hard-errors on staleness).
#
# Usage:
#   scripts/strictspec_gen.sh          # regenerate + check all manifests
#   scripts/strictspec_gen.sh --check  # check only (no regeneration; CI freshness gate)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Prefer the venv console script (the PyPI launcher fetches the pinned Go
# toolchain on first run); fall back to PATH.
if [[ -x "$ROOT/.venv/bin/strictspec" ]]; then
  STRICTSPEC="$ROOT/.venv/bin/strictspec"
else
  STRICTSPEC="strictspec"
fi

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

status=0
for manifest in */strictspec.toml; do
  pkg="$(dirname "$manifest")"
  echo "== $pkg =="
  if [[ "$CHECK_ONLY" -eq 0 ]]; then
    ( cd "$pkg" && "$STRICTSPEC" gen )
  fi
  if ! ( cd "$pkg" && "$STRICTSPEC" check ); then
    status=1
  fi
done

exit "$status"
