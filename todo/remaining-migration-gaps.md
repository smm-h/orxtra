# Remaining migration gaps (split from claudestream-migration-gaps.md)

Three items from the original claudestream migration gap analysis that were not addressed during the Phase 0-15 implementation.

## @tool decorator / auto-schema from type hints

No `@tool` decorator exists. All 25 tools use hand-written JSON Schema dicts for input parameters and manual `make_*_tool` factory constructors. The `ToolOutput[T]` migration (Phase 1) added typed return values and semantic result types, but the input side is still untyped.

The infrastructure is partially ready: Pydantic input models were planned but not created, the `@tool` decorator producing `ToolTemplate[T]` with `.bind(**deps)` was designed but not implemented, and `_schema_gen.py` (type hints to JSON Schema) was planned but not built.

21 of 25 tools are eligible for the decorator pattern (they use simple closure-based dependency injection). 4 tools (exec, shell, http, consult) need to stay as factories due to dynamic schema construction.

## Schema dual maintenance (pgdesign TOML vs Python DDL)

`schema/orxtra.toml` and `trace/src/orxtra/trace/_schema.py` are maintained in parallel with 7 documented column-level divergences (UUID function, enum types, NUMERIC precision, defaults, indexes, immutability approach, FK style). A table-level sync check exists in CI (`scripts/check_schema_sync.py`) but column-level reconciliation is not automated.

Blocked on pgdesign shipping a Python DDL codegen mode (todo filed in pgdesign: `todo/python-ddl-and-query-layer-codegen.md`).

## Knowledge content hash cache persistence

`overseer/src/orxtra/overseer/_knowledge.py` uses `_loaded_hashes` as a module-level in-memory dict for tracking which knowledge files have been loaded. This cache is lost on process restart, causing redundant re-loading. The original knowledge-module had a PG-backed `ContentHashCache` (via the now-deleted `knowledge_hashes` table), but that was removed along with the module.

Options: persist hashes via StorageBackend, or accept re-loading on restart (it's idempotent, just wasteful).
