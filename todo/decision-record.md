# Decision record — implementation program (July 2026)

Authoritative record of the design decisions made for the implementation program described
in `implementation-plan.md`. Decisions here are settled; the plan document sequences them.
Both documents supersede the original exploratory todos (moved to `todo/.obsolete/`):
context-assembly-engine, data-defined-tools, event-bus-http-layer, schema-dual-maintenance,
strictcli-tool-bridge, tool-graph-advanced.

## Schema track

- **Migration discipline from now on.** No drop-and-recreate. Schema changes are expressed
  as migrations against live data via pgdesign's diff-based `migrate` machinery
  (`pgdesign_migrations` tracking table).
- **Adopt pgdesign codegen (faceted mode), committed output.** The hand-written
  `trace/_schema.py` and `dispatch/_schema.py` are deleted and replaced by
  pgdesign-generated Python DDL modules. `pgdesign codegen --check` becomes the CI
  freshness gate; `scripts/check_schema_sync.py` is deleted. Generated files must pass
  mypy --strict; ruff gets per-file-ignores scoped to generated paths with written
  rationale.
- **ON DELETE mixed by table class.** RESTRICT into audit tables (events, transcripts,
  notepad_entries — already applied in trace.toml), CASCADE for operational state.
  The intentional mix is recorded via `[suppress]` entries for W014/W015 with rationale.
- **Provisioning via `orxtra db` CLI commands**: `db init` (generated executor,
  trace → dispatch → auth order), `db verify`, `db migrate` (wrapping pgdesign migrate).
  A shared startup-verification helper hard-errors every DB-backed process (serve, CLI,
  dispatcher, incoming) against a missing/outdated schema with an actionable message.
  Never auto-create schema at app startup.

## Data-defined tools track

- **Two execution types**: declarative `http` and `monty` (pydantic-monty code engine).
  The originally-sketched `command`/`script`/`composite` types are subsumed by monty
  capabilities. pydantic-monty is **pinned** to a known-good version, bumped deliberately
  (external experimental dep; the unpinned-latest rule applies to internal tools only).
- **Capabilities are wrapped built-in tools.** A monty tool's `file`/`http`/`command`
  capability IS the corresponding built-in tool invoked through the shared pipeline core:
  write queue, path scopes, safegit/saferm, secret scrubbing, tracing apply mechanically.
  Bypass is structurally impossible. The `command` capability (pinned executable +
  arg-validation config) is backed by the relocated subprocess machinery from the old
  exec tool and is the execution path for absorbed `[[exec]]`-style tools.
- **Effect tags are derived, never declared.** monty tools: from granted capabilities.
  http tools: from HTTP method (GET/HEAD → readonly, else mutation). Consult-stripping
  and mutation tracking migrate from hardcoded name-sets to tag/capability-derived logic.
- **Output schemas are enforced validation** — mismatch is a hard ToolError.
- **`[[exec]]`/`[shell]` absorbed and deleted.** Agent TOMLs may embed full new-style
  definitions inline (`[[tools.define]]`, same schema/loader as standalone files;
  per-agent scope; name collisions hard-error). ExecToolConfig/ShellConfig and both tool
  constructors are deleted; subprocess internals survive only as the command-capability
  backend.
- **Loading**: services loads from an explicit `tools_dir` RunConfig key and registers
  tools (with full metadata) before Scheduler construction. The scheduler never touches
  the filesystem for definitions.
- **Reserved namespace root: `custom`.** Data tools must declare namespaces under it;
  built-in wildcards (`fs.*`) can never match them; agents opt in via `custom.*` or
  explicit names.
- **Load-time hard error on unknown `{{secret:NAME}}` references** in definitions.
- **Security/trust hardening deliberately deferred** (capability-gating manifests,
  tools-dir protections) until the functional design exists — Phase 9.

## Event-bus track

- **Dedicated dispatcher worker** executes persistent subscription actions for DB-written
  events (the two event paths never meet today). Consumer loop lives in dispatch against
  the ActionExecutor protocol; services wires ServicesActionExecutor +
  AsyncioFlushScheduler; the CLI registers **`orxtra dispatch run`** (new `dispatch`
  group) with a graceful stop path. Durable cursor + per-event-action completion records
  in a **dispatch-owned** claim table. Honest guarantee: at-least-once on crash
  (completion records make re-execution detectable/skippable); duplicate deliveries are
  deduplicated upstream at insert time.
- **Idempotency key column on events**, atomic insert-or-skip (ON CONFLICT DO NOTHING),
  protecting storage and dispatch for every ingestion path.
- **Auth: bearer/api_key + HMAC via the capability-keys design** (solution 10 of the
  evaluated ladder):
  - `KeyedMacProvider` protocol (in protocols/): non-exportable versioned keys modeled on
    KMS — the only operation is `verify(key_ref, message, signature) -> MacVerdict`;
    no get-value operation exists in any type, so key export is impossible by
    construction. Multiple concurrently-valid key versions make rotation first-class;
    verdicts report the matched version. The env-mapping adapter over SecretRegistry is
    the first backend; vault/KMS backends are future implementations of the same
    protocol (explicit mode selection, no fallback).
  - `CredentialVerifier` per-credential-type strategy protocol; Authenticator becomes a
    thin dispatcher over a registry populated at composition. Unregistered credential
    type is a construction-time hard error. hash-at-rest types visibly need zero secret
    capability.
  - Every verification emits an audit event via the existing EventSink seam into trace's
    append-only store (key name, credential/source, outcome, matched version).
  - Auth's backend union becomes an `AuthStorage` protocol. **Auth depends on protocols
    only** — no auth→secrets dependency; secrets implements the provider contract.
  - Schema: `credential_type` enum gains `hmac`; credentials gain an explicit
    `secret_ref` column (not smuggled through metadata).
  - Known bug fixed on the way: middleware/Authenticator never pass the pool the PG
    AuthBackend requires.
- **Single-operator scope model.** Coarse scope vocabulary (events:read/events:write/
  sources:manage-style), enforced via the currently-dead Authorizer. Sources stay global;
  multi-party isolation is a future retrofit only if needed.
- **Per-source mapping config** (strict-pydantic `config` jsonb column on sources) for
  event_type extraction; missing mapping or field = 400, no fallback.
  `credential_id = NULL` sources are rejected (403) by the HTTP receiver.
- **New sub-project `incoming/`** (`orxtra.incoming`, interfaces layer): webhook receiver
  (`POST /events/{slug}`, raw-body HMAC verification, body-size cap, 202), replay
  endpoint over trace's cursor-based `replay()`, SSE stream with hand-built catch-up
  (subscribe-first, replay to cursor, overlap-dedup, fetch-on-notify — NOTIFY payload
  lacks data by design). Mounted by the api compositor (agui pattern);
  api→incoming edge declared.
- Latent bugs to fix en route: `services.event_stream` wrong implicit default channel
  (replace with a shared constant parity-tested against the committed generated trigger
  DDL; no default parameter); accumulator buffering fabricates fresh event ids instead of
  carrying trace's; missing `get_source_by_slug` service function.

## Composition engine track

- **New sub-project `compose/`** (`orxtra.compose`, foundation layer, **zero
  intra-workspace deps**): fragment model with static file fragments and
  runtime-parameterized providers via a provider protocol compose defines. Trace-backed
  providers live above it — compose never imports trace.
- **Full subsumption** of the three existing composition mechanisms: the agent loader's
  include/variable composition (ported with its strict semantics; `agent/_prompt.py`
  deleted), the scheduler's `_assemble_agent_prompt` layering (all hard-coded prompt
  strings — section headers, escalation/handoff/resume messages, overseer refine wrapper,
  task-dispatch notification — move to packaged `.md` templates), and the overseer
  knowledge loader (hash-gated fragment source; its silent no-op for hash-less writers
  and implicit tier/kind defaults are fixed).
- **Strict substitution everywhere** after a corpus sweep: the scheduler's lenient
  `_resolve_prompt` is unified with the strict semantics; sweep happens before the flip.
- **Notepad rendering moves to a compose template + provider**; the notepad module keeps
  a data-only API. No notepad→compose edge.
- **The dead injection points get wired**: `_active_constraints`/`_lessons`/
  `_notepad_entries` (populated by nothing in production today) are fed by providers
  **constructed in services** (composition layer — legally imports scheduler and
  overseer; `filter_stale_lessons` stays in overseer) and injected into the scheduler.
- agent→compose and scheduler→compose edges declared everywhere (workspace depends_on,
  pyproject, uv sources, docs layer-table exception list).

## Tool graph track

- **All edges advisory, never enforced.** Real prerequisites belong in task pre-checks
  (already enforced). Inferred and declared edges may mix later; inference and semantic
  discovery are a Phase 9 design round (advisory-only, explicit thresholds).
- **Deferred tools are declared per-agent in the agent TOML** (not registry-global, not
  scheduler policy). `load_tools` is reworked to factory-based lazy building with
  allow-list scoping enforced inside it and pipeline-wrapping applied at load; granted
  automatically when an agent declares deferred tools. ToolEntry gains
  description/deferred.
- **Surfacing**: deferred stubs + result-appendix suggestions from packaged `.md`
  templates, deduped per session. Never auto-loading (never silently mutate the tool set).
- **Run-start validation**: unknown explicit allow-list names hard-error at Scheduler
  construction across all agents; unknown tag names hard-error too (tags are a finite
  derivable vocabulary); zero-matches on known tags/wildcards pass. The validator is
  explicitly updated when exec/shell special cases are deleted and when deferred
  declarations arrive.
- `register_custom` carries real namespace/tags and deps-aware factories (currently
  hardcoded empty).

## Cross-cutting hardening

- **Secret scrubbing extends to structured data globally** (serialized `result.data`) and
  to the end_task task-output path — implemented once, in a shared pipeline core
  extracted from the duplicated `tool/_pipeline.py` / `worker/_pipeline_split.py`,
  with a drift-sentinel test.
- **SecretRegistry construction is a run-independent factory** (explicit env-var-name
  mapping) consumed by start_run, the serve lifecycle, and incoming — one construction
  path. Production currently passes secret_registry=None everywhere; that gets wired.
- **Dev-loop fix: derived meta-build** (solution 10 of the evaluated ladder). The root
  wheel's force-include is removed from config; a build hook injects the aggregation
  mapping only for standard (release) builds, **derived at build time from workspace
  members**; sub-project members install via a PEP 735 dev-group so plain `uv sync`
  produces a correct dev venv with zero materialized snapshot files. CI conformance
  checks: dev-group == workspace members; built-wheel top-level contents == member set.
  This also fixes CI currently importing the stale snapshot rather than source trees.
- **check_imports.py reads workspace.toml's `[layers.assignments]`** instead of a
  hand-maintained LAYERS dict.
- Docs (CLAUDE.md/README) are selfdoc-generated from templates; a CI consistency check
  compares the template's layer table against workspace.toml.

## strictcli bridge track

- **Deferred behind a fresh analysis.** The June ten-solution comparison is re-run
  against current strictcli with this program's outcomes as constraints (data-tools
  definition schema, monty engine, wrapped-capability posture, registry shape), with an
  open outcome — including whether a shared definition package is warranted at all.
  Options return to the owner for decision; naming is the owner's.

## Post-implementation cleanup (July 2026)

Systematic cleanup pass after Phases 0–7 implementation and audit. Five phases (0–4)
addressing test infrastructure, type safety, API consistency, and integration gaps.

Fixes landed:
- PG fixture: pg_fixtures.py updated to apply auth schema (three-schema ordering:
  trace, dispatch, auth) with credential_type enum and auth table round-trip test.
- Protocols mypy: fixed mypy --strict violations in protocols module (EventBus
  re-export, type annotations).
- Private imports: replaced cross-module private imports with public API surfaces
  throughout the codebase.
- AuthStorage protocol: introduced AuthStorage protocol in protocols replacing the
  concrete backend union in auth; auth depends on protocols only.
- format_notepad deleted: removed dead format_notepad function from notepad; notepad
  exposes data-only API.
- Strict substitution: unified substitution semantics; _resolve_prompt filters unused
  variables (accommodation for workflow dependency accumulation pattern) rather than
  erroring on them.
- EventBus multi-callback: added unsubscribe() to EventBus protocol; implemented on
  both InMemoryBackend and PgBackend.
- Serve-lifecycle auth wiring: wired --secrets-env flag through serve lifecycle to
  auto-construct the full auth stack (KeyedMacProvider, CredentialVerifier registry,
  Authenticator, Authorizer) from environment secrets.
- PG auth tests: added PG round-trip tests for AuthBackend covering credential CRUD
  and lookup operations.

## Out of scope / rejected

- Gateway provider for the external consumer: rejected for this repo; the structural
  Provider protocol lets the consumer implement it with zero orxtra changes
  (todo moved to `.obsolete/`).
- The context-engine's multi-runtime vision (Claude Code/Cursor/OpenCode delivery,
  tool-shipped context packages, signing): out of orxtra's scope; orxtra's compose module
  is internal-only.
- Multi-tenancy: not built; single-operator explicit; revisit only if the assumption
  falls.
