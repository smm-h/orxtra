#!/usr/bin/env python3
"""Check that pgdesign TOML and trace/_schema.py define the same tables.

Lightweight table-level parity check. Full column-level reconciliation will
happen when pgdesign gets Python DDL codegen.

Requires pgdesign to be installed (Go binary).
"""

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOML_PATH = ROOT / "schema" / "orxtra.toml"
SCHEMA_PY_PATH = ROOT / "trace" / "src" / "orxtra" / "trace" / "_schema.py"


def get_pgdesign_tables() -> set[str]:
    """Run pgdesign generate and extract CREATE TABLE names."""
    pgdesign = shutil.which("pgdesign")
    if pgdesign is None:
        print("ERROR: pgdesign not found in PATH", file=sys.stderr)
        sys.exit(2)
    result = subprocess.run(
        [pgdesign, "generate", str(TOML_PATH)],
        capture_output=True,
        text=True,
        check=True,
    )
    tables: set[str] = set()
    for line in result.stdout.splitlines():
        m = re.match(r"CREATE TABLE\s+(?:public\.)?(\w+)\s*\(", line)
        if m:
            tables.add(m.group(1))
    return tables


def get_python_ddl_tables() -> set[str]:
    """Parse TABLE_NAMES dict from _schema.py source."""
    source = SCHEMA_PY_PATH.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # TABLE_NAMES: dict[str, str] = {...} is AnnAssign.
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "TABLE_NAMES"
            and isinstance(node.value, ast.Dict)
        ):
            tables: set[str] = set()
            for v in node.value.values:
                if isinstance(v, ast.Constant) and isinstance(
                    v.value, str
                ):
                    tables.add(v.value)
            return tables
    print(
        "ERROR: Could not find TABLE_NAMES dict in _schema.py",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    pgdesign_tables = get_pgdesign_tables()
    python_tables = get_python_ddl_tables()

    ok = True

    only_pgdesign = pgdesign_tables - python_tables
    if only_pgdesign:
        print(
            "Tables in pgdesign TOML but missing from _schema.py:"
            f" {sorted(only_pgdesign)}"
        )
        ok = False

    only_python = python_tables - pgdesign_tables
    if only_python:
        print(
            "Tables in _schema.py but missing from pgdesign TOML:"
            f" {sorted(only_python)}"
        )
        ok = False

    if ok:
        count = len(pgdesign_tables)
        print(f"Schema sync OK: {count} tables match in both sources.")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
