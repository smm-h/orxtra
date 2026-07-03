# Schema dual maintenance (pgdesign TOML vs Python DDL)

`schema/trace.toml` and `trace/src/orxtra/trace/_schema.py` are maintained in parallel. `schema/dispatch.toml` owns dispatch-module tables (sources, subscriptions, subscription_actions, accumulator_buffer). The schema sync check (`scripts/check_schema_sync.py`) validates both owners at the table level but column-level reconciliation is not automated.

## Resolved divergences

The following divergences between pgdesign output and _schema.py have been resolved:

- **UUID generation**: pgdesign now emits `uuid_generate_v7()` via a shadowed `id` type and pg_uuidv7 extension declaration in trace.toml.
- **NUMERIC precision**: pgdesign now emits `numeric(12, 6)` for the `amount` type.
- **NOTIFY trigger**: pgdesign now emits the `notify_orxtra_event()` function and `trg_notify_event` trigger on the events table.

## Remaining divergences (4)

- Enum columns: pgdesign emits proper `CREATE TYPE ... AS ENUM`; _schema.py uses TEXT.
- Default values: pgdesign emits explicit defaults on some JSONB columns; _schema.py omits them.
- Indexes: pgdesign emits indexes for all tables; _schema.py only has events indexes.
- Immutability approach: pgdesign uses deny-mutation triggers; _schema.py uses `REVOKE UPDATE, DELETE`.
- FK style: pgdesign emits `ALTER TABLE ... ADD CONSTRAINT`; _schema.py uses inline `REFERENCES`.

Blocked on pgdesign shipping a Python DDL codegen mode (todo filed in pgdesign: `todo/python-ddl-and-query-layer-codegen.md`).
