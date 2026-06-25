#!/usr/bin/env python3
"""Check that pgdesign TOML schema files and Python _schema.py DDL files
define the same tables.

Lightweight table-level parity check. Full column-level reconciliation will
happen when pgdesign gets Python DDL codegen.

Supports multiple schema owners (trace, dispatch) each with their own
TOML file and _schema.py. Parses [tables.*] keys from each TOML file to
determine ownership, then checks each owner's _schema.py TABLE_NAMES dict
for parity.

Requires pgdesign to be installed (Go binary).
"""

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schema"

# Each owner maps to (toml_file, _schema.py path).
SCHEMA_OWNERS: dict[str, tuple[Path, Path]] = {
    "trace": (
        SCHEMA_DIR / "trace.toml",
        ROOT / "trace" / "src" / "orxtra" / "trace" / "_schema.py",
    ),
    "dispatch": (
        SCHEMA_DIR / "dispatch.toml",
        ROOT / "dispatch" / "src" / "orxtra" / "dispatch" / "_schema.py",
    ),
}


def get_toml_tables(toml_path: Path) -> set[str]:
    """Parse [tables.*] keys from a pgdesign TOML schema file."""
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    tables_section = data.get("tables", {})
    return set(tables_section.keys())


def validate_with_pgdesign() -> None:
    """Run pgdesign generate on the schema directory to verify validity."""
    pgdesign = shutil.which("pgdesign")
    if pgdesign is None:
        print("ERROR: pgdesign not found in PATH", file=sys.stderr)
        sys.exit(2)
    result = subprocess.run(
        [pgdesign, "generate", str(SCHEMA_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            "ERROR: pgdesign generate failed on schema directory:",
            file=sys.stderr,
        )
        print(result.stderr, file=sys.stderr)
        sys.exit(2)
    # Count total tables as a sanity check.
    table_count = sum(
        1
        for line in result.stdout.splitlines()
        if re.match(r"CREATE TABLE\s+", line)
    )
    return table_count


def get_python_ddl_tables(schema_py: Path) -> set[str]:
    """Parse TABLE_NAMES dict from a _schema.py source file."""
    if not schema_py.exists():
        return set()
    source = schema_py.read_text()
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
        f"ERROR: Could not find TABLE_NAMES dict in {schema_py}",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    # Validate the full schema directory with pgdesign first.
    total_pgdesign = validate_with_pgdesign()

    ok = True
    total_checked = 0

    for owner, (toml_path, schema_py) in SCHEMA_OWNERS.items():
        if not toml_path.exists():
            print(f"ERROR: {toml_path} does not exist", file=sys.stderr)
            sys.exit(2)

        toml_tables = get_toml_tables(toml_path)
        python_tables = get_python_ddl_tables(schema_py)

        if not python_tables and not schema_py.exists():
            print(
                f"  {owner}: _schema.py not found at {schema_py},"
                f" skipping ({len(toml_tables)} TOML tables unchecked)"
            )
            continue

        only_toml = toml_tables - python_tables
        if only_toml:
            print(
                f"  {owner}: tables in TOML but missing from _schema.py:"
                f" {sorted(only_toml)}"
            )
            ok = False

        only_python = python_tables - toml_tables
        if only_python:
            print(
                f"  {owner}: tables in _schema.py but missing from TOML:"
                f" {sorted(only_python)}"
            )
            ok = False

        if not only_toml and not only_python:
            print(
                f"  {owner}: OK ({len(toml_tables)} tables match)"
            )
            total_checked += len(toml_tables)

    if ok:
        print(
            f"Schema sync OK: {total_checked} tables checked"
            f" ({total_pgdesign} total in pgdesign)."
        )
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
