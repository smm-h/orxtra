#!/bin/bash
set -e
for mod in protocols secrets write-safety transport agent tool verify trace notepad session scheduler overseer knowledge-module services cli mcp; do
  echo "=== $mod ==="
  cd /home/m/Projects/orxt/$mod
  MYPYPATH=src uv run --with mypy --with 'pydantic[mypy]' python -m mypy --strict --explicit-package-bases src/orxt/*/
  cd /home/m/Projects/orxt
done
echo "All modules passed mypy --strict"
