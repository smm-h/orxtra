#!/bin/bash
# Verify pgdesign-generated DDL files are fresh.
#
# Wraps `pgdesign codegen --check` with awareness of the __init__.py file
# that we maintain alongside the generated output. pgdesign flags it as an
# orphan because it didn't generate it, but it's required for Python package
# imports (the generated schema_executor.py uses relative imports).
#
# Once pgdesign generates __init__.py itself (filed as pgdesign todo),
# this wrapper can be replaced with a bare `pgdesign codegen --check`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="$REPO_ROOT/schema/_generated"
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

# Missing or stale files are hard errors
if [ "$missing" -gt 0 ] || [ "$stale" -gt 0 ]; then
    echo "FAIL: $missing missing, $stale stale" >&2
    exit 1
fi

# Orphans: only __init__.py is expected; any others are errors
if [ "$orphans" -gt 1 ]; then
    echo "FAIL: unexpected orphan files (only __init__.py is expected)" >&2
    exit 1
fi

if [ "$orphans" -eq 1 ]; then
    # Verify the orphan IS __init__.py
    if ! echo "$output" | grep -q '\[orphan\].*__init__\.py'; then
        echo "FAIL: orphan is not __init__.py" >&2
        exit 1
    fi
fi

echo "Schema codegen check passed"
