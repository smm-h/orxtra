#!/bin/bash
# Verify pgdesign-generated DDL files are fresh.
#
# The generated package ships inside the orxtra namespace at
# services/src/orxtra/services/_generated so it is importable as
# `orxtra.services._generated` from both the editable dev tree and an
# installed wheel (the `services` package is force-included whole by
# hatch_build.py). pgdesign 0.25+ generates the package __init__.py itself,
# so there are no orphan files to special-case.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="$REPO_ROOT/services/src/orxtra/services/_generated"
CONFIG="$REPO_ROOT/schema/pgdesign.toml"

output=$(pgdesign codegen --lang python --mode ddl --split-mode faceted \
    --check --output "$OUTPUT_DIR" "$CONFIG" 2>&1) || true

echo "$output"

# Extract the summary line: "N file(s): X missing, Y stale, Z fresh; W orphan(s)"
summary=$(echo "$output" | grep -E '^[0-9]+ file\(s\):')

if [ -z "$summary" ]; then
    echo "ERROR: could not parse pgdesign codegen --check output" >&2
    exit 1
fi

# Extract counts
missing=$(echo "$summary" | grep -oP '\d+ missing' | grep -oP '^\d+')
stale=$(echo "$summary" | grep -oP '\d+ stale' | grep -oP '^\d+')
orphans=$(echo "$summary" | grep -oP '\d+ orphan' | grep -oP '^\d+' || echo "0")

# Missing, stale, or orphan files are all hard errors: pgdesign owns every
# file in the output directory, including __init__.py.
if [ "$missing" -gt 0 ] || [ "$stale" -gt 0 ] || [ "$orphans" -gt 0 ]; then
    echo "FAIL: $missing missing, $stale stale, $orphans orphan(s)" >&2
    exit 1
fi

echo "Schema codegen check passed"
