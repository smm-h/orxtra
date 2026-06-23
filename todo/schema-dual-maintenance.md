# Schema dual maintenance (pgdesign TOML vs Python DDL)

`schema/orxtra.toml` and `trace/src/orxtra/trace/_schema.py` are maintained in parallel with 7 documented column-level divergences (UUID function, enum types, NUMERIC precision, defaults, indexes, immutability approach, FK style). A table-level sync check exists in CI (`scripts/check_schema_sync.py`) but column-level reconciliation is not automated.

Blocked on pgdesign shipping a Python DDL codegen mode (todo filed in pgdesign: `todo/python-ddl-and-query-layer-codegen.md`).
