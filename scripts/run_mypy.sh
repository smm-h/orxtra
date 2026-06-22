#!/bin/bash
set -e
for mod in protocols secrets write-safety transport agent tool verify trace notepad session scheduler overseer services cli mcp; do
  echo "=== $mod ==="
  cd /home/m/Projects/orxtra/$mod
  MYPYPATH=src uv run --with mypy --with 'pydantic[mypy]' python -m mypy --strict --explicit-package-bases src/orxtra/*/
  cd /home/m/Projects/orxtra
done
echo "All modules passed mypy --strict"
