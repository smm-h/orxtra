# Delivery + Worker + AG-UI Live Streaming — Implementation Plan

## Locked decisions

1. FilterPredicate gains `principal_id: UUID | None` for self-subscription filtering; AND-combined with all other predicates.
2. WebSocket auth extends auth_middleware for websocket scopes; reject with code 4001 before accept. ASGI flow: headers are on scope["headers"] without consuming any message; extract bearer, authenticate; if INVALID then consume websocket.connect and send websocket.close with code 4001; if VALID store auth context and pass through with receive unconsumed so ws.accept() works normally in the handler.
3. ToolLocation enum (LOCAL/ANYWHERE — REMOTE removed, no tool uses it) and ToolCapability enum on ToolEntry in scheduler/_tool_registry.py (routing decisions happen before tool construction) and Tool (factory propagates). TaskSpec.execution_target: str | None replaces TaskSpec.remote: bool.
4. New Foundation-layer notification module behind NotificationPort protocol; notification_deliveries table in schema/notification.toml with opaque source_ref (no upward FK); PG NOTIFY trigger on global channel orxtra_notifications with client-side principal filtering (no per-principal channels).
5. NotifyAction as fifth action type; receives NotificationPort via injection; handler calls port.create_delivery().
6. Self-subscription: create_principal accepts `notification_event_types: list[str] | None` (required for consumer/app-registered kinds, must be None for run/source/system). The caller passes ONLY the event types list — create_principal internally mints the principal, then constructs FilterPredicate(event_types=..., principal_id=minted.id) + NotifyAction(target_principal_id=minted.id) + the subscription. The caller never touches FilterPredicate or NotifyAction (the principal's id is DB-generated and unknowable to the caller). Exact new signature: create_principal(dispatch_backend, principal_storage, kind_registry, caller_principal, *, kind, external_ref, display_name=None, notification_event_types=None).
7. Principal notifications SSE endpoint replicating incoming's catch-up pattern against notification_deliveries.
8. RunManager instance-scoped on DispatchContext; demand-driven subscribe(run_id, sinks) → unsubscribe; Scheduler gains add/remove sink methods; new sessions auto-receive registered transport sinks; safe iteration via list snapshot copy.
9. Completed runs get enriched StateSnapshotEvent only (terminal metadata from read_run_report); no trace replay, no second translation path.
10. Workers authenticate as consumers via extended middleware; get_worker_bridge as a synchronous callback injected via capability inject tokens (no layer violation).

---

## Phase 0 — Foundational groundwork

### 0.1 — Notification scope constants
Add `SCOPE_NOTIFICATIONS_READ` and `SCOPE_NOTIFICATIONS_MANAGE` to the scope vocabulary in `protocols/_types/_auth.py`; add both to `ALL_SCOPES`; export through both `__init__` chains; update the scope-pinning test in `auth/tests/test_auth.py`.

Verify: both constants in ALL_SCOPES; imports resolve; pinning test green.

### 0.2 — All new inject tokens + DispatchContext fields + NotificationPort protocol + PgEventBus lifecycle (merged from prior 0.2+0.3 — both touch the same data structures)
One pass adding everything to avoid parallel-agent collision on the same files:
- `VALID_INJECT_TOKENS` gains: `"notification_port"`, `"get_worker_bridge"`, `"run_manager"`.
- `_INJECT_ORDER` becomes: pool(0), dispatch_backend(1), principal_storage(2), kind_registry(3), notification_port(4), get_worker_bridge(5), run_manager(6), caller_principal(7).
- `_INJECT_LABELS` gains entries for all three new tokens.
- `DispatchContext` gains: `notification_port: NotificationPort | None = None`, `get_worker_bridge: Callable[[str], Any] | None = None`, `run_manager: Any | None = None`.
- `NotificationPort` protocol added to `protocols/_contracts.py` (runtime-checkable; methods: `create_delivery`, `list_for_principal`, `acknowledge`). Exported from protocols.
- `get_worker_bridge` is a synchronous callback (WorkerRegistry is in-memory, no I/O; called from the synchronous _build_agent_tools context).
- `PgEventBus(pool)` constructed in `api/_lifecycle.py` and set as `event_bus` on DispatchContext — this field exists but is NEVER populated in the current lifecycle (a latent gap; both the notification SSE and the incoming SSE need it for LISTEN/NOTIFY). This is infrastructure, not notification-specific, so it belongs here. ALSO: update `create_incoming_router(...)` call at `_lifecycle.py:135-140` to include `event_bus=event_bus` (currently not passed — incoming SSE is non-functional in production). Shutdown: `await event_bus.close()` in the finally block BEFORE `pool.close()` (EventBus releases listener connections back to the pool first).
- The notification module depends on the EventBus PROTOCOL from protocols only, never on PgEventBus (trace) directly.

Verify: Capability declaring any new inject token accepted; DispatchContext(notification_port=mock, get_worker_bridge=fn, run_manager=obj, event_bus=mock) works; existing dispatch unchanged; protocol importable; existing tests unaffected (all default to None).

---

## Phase 1 — FilterPredicate.principal_id + WebSocket auth

Parallel with Phases 2 and 3; disjoint files.

### 1.1 — FilterPredicate.principal_id
Add `principal_id: UUID | None = None` to FilterPredicate in `protocols/_types/_dispatch.py`. In `dispatch/_delivery.py` match_subscription: after the sources check, add a principal_id branch — if `filter_predicate.principal_id is not None`, return False when it does not equal `event.principal_id`. This is AND-combined: the event must match principal_id AND event_types AND sources (whichever are set; None = wildcard on that axis). Both backends' serialization works unchanged (PG stores filter as JSON via model_dump; the new field defaults to None so existing rows deserialize cleanly). Tests: match with principal_id set; match with None (wildcard); mismatch rejects; combined with event_types (both must match).

Verify: red-green match tests; existing FilterPredicate tests pass unchanged; both backends round-trip the field.

### 1.2 — WebSocket auth in middleware
Extend `auth/_middleware.py` for `scope["type"] == "websocket"`. CRITICAL ASGI flow (a prior critique caught a deadlock in the naive approach): headers are already on `scope["headers"]` — NO message consumption needed for auth. Extract bearer via `_extract_bearer_token` from `scope["headers"]` (not from receive). Authenticate. If INVALID: consume `websocket.connect` via `await receive()`, then send `{"type": "websocket.close", "code": 4001}` — the connection is rejected during the handshake. If VALID: store AuthContext in `scope["state"]["auth_context"]` and call `await app(scope, receive, send)` with receive UNCONSUMED — so the handler's `ws.accept()` (which internally calls receive expecting websocket.connect) works normally. Lifespan scopes still pass through.

Verify: red-green — authenticated WebSocket passes (AuthContext in scope state); unauthenticated rejected at 4001 before accept; HTTP behavior unchanged; lifespan untouched.

---

## Phase 2 — ToolLocation + capabilities + ExecutionTarget

Parallel with Phases 1 and 3.

### 2.1 — Enums + ToolEntry/Tool fields
`ToolLocation` (LOCAL, ANYWHERE — REMOTE removed, no existing tool uses it; add if a use case materializes) and `ToolCapability` (READ, WRITE, EXEC, HTTP, GIT) as StrEnums in `protocols/_types/_tool.py`. ToolEntry (defined in `scheduler/src/orxtra/scheduler/_tool_registry.py`, NOT tool/_registry.py which does not exist) gains `location: ToolLocation = ToolLocation.ANYWHERE` and `capabilities: frozenset[ToolCapability] = frozenset()` — routing decisions happen in `_build_agent_tools` before tool construction, so the metadata must live on ToolEntry. The factory propagates both fields to the constructed Tool (Tool also gains the fields). Every ToolEntry in `scheduler/_tool_registry.py` declares both: lifecycle tools = LOCAL, filesystem read tools = ANYWHERE + {READ}, filesystem write tools = ANYWHERE + {WRITE}, exec = ANYWHERE + {EXEC}, git = ANYWHERE + {GIT}, http = ANYWHERE + {HTTP}, notepad/trace-mutating = LOCAL. WorkerRegistration gains `capabilities: list[ToolCapability]` replacing `list[str]`. Exports through protocols.

Verify: every tool in the registry declares location + capabilities; WorkerRegistration validates capability enum values; existing tests pass (defaults are backward-compatible).

### 2.2 — TaskSpec.execution_target + resolver + sweep
Replace `TaskSpec.remote: bool = False` with `execution_target: str | None = None` (the string names a worker root; None = all-local). `execution_target` defaults to None so existing TOML workflow files without the field work unchanged (extra='forbid' only rejects unknown keys, not missing optional ones). Sweep all `TaskSpec.remote` references: `worker/tests/test_worker.py`, plus `examples/*.toml` and test fixture TOMLs (verify none use `remote = true`). `should_route_to_worker(tool_entry, task) -> bool` composition function (returns True = wrap with bridge, False = local pipeline): task has no target → False; tool is LOCAL → False regardless of target; tool is ANYWHERE + task has target → True.

Verify: resolver tested for every combination; grep zero `.remote` on TaskSpec (excluding the deletion diff itself); examples and fixture TOMLs clean; full suite green.

---

## Phase 3 — Notification module (Foundation)

Parallel with Phases 1 and 2.

### 3.1 — Module scaffold
Full monorepo registration per the established checklist (following identity/ as template): `notification/pyproject.toml` (depends on orxtra-protocols + asyncpg + uuid6), root pyproject (sdist include, workspace member, uv source, dev group), workspace.toml (Foundation layer, depends_on = ["protocols"]), run_mypy.sh, docs layer table + selfdoc gen, per-project ci.yml, rlsbl monorepo sync, uv lock.

Verify: `from orxtra.notification import __version__`; check_imports green; check_layer_docs green; CI router includes notification.

### 3.2 — Schema + codegen
`schema/notification.toml`: notification_deliveries table (id uuidv7, target_principal_id FK RESTRICT principals, source_ref text, payload jsonb, created_at timestamp, acknowledged_at nullable timestamp). Indexes: (target_principal_id, created_at) WHERE acknowledged_at IS NULL; source_ref. PG NOTIFY trigger function + trigger on INSERT: channel `orxtra_notifications` (global, following the incoming pattern), payload = JSON with notification_id + target_principal_id. Registered in pgdesign.toml; codegen regenerated.

Verify: check_schema_codegen green; db init/db verify see the table.

### 3.3 — PG + in-memory backends implementing NotificationPort
PgNotificationBackend: create_delivery (INSERT + return id), list_for_principal (SELECT WHERE target + unacked + optional cursor, ordered by created_at, limited), acknowledge (UPDATE SET acknowledged_at = now(), 0-row = hard error). InMemoryNotificationBackend: dict-backed, same semantics. Shared parametrized test suite running against both backends (the dispatch backend testing pattern). Parity guard covering the protocol methods via signature inspection.

Verify: round-trip tests both backends (create → list → ack → list-returns-empty); parity guard green.

### 3.4 — Wire into lifespan + CRUD capabilities
api/_lifecycle.py: construct PgNotificationBackend(pool), pass as notification_port on DispatchContext. (PgEventBus construction already handled in Phase 0.2.) Service functions in services/_notifications.py: list_deliveries (scoped to caller principal via caller_principal injection — hard error if asking for another principal's deliveries), acknowledge_delivery (scoped — hard error if delivery doesn't belong to caller). Params models. Capability registrations under notifications:read (list) and notifications:manage (ack) with appropriate injects + scopes.

Verify: dispatch of list/ack capabilities works; scoping enforced; registry pins updated.

---

## Phase 4 — NotifyAction

After Phase 3.

### 4.1 — Model + serialization
NotifyAction model in protocols/_types/_actions.py: target_principal_id (UUID), source_ref (str), payload (dict). Added to the Action union type alias AND the `_ActionType` type alias AND `_serialize_action`'s parameter type annotation in dispatch/_pg_backend.py. PG type/class maps ("notify" ↔ NotifyAction) in dispatch/_pg_backend.py. Export through protocols.

Verify: serialization round-trip; Action union type-checks with NotifyAction; mypy clean.

### 4.2 — Execution + dispatch worker wiring
isinstance branch in execute_action; the handler receives notification_port via a new kwarg and calls port.create_delivery(target, source_ref, payload). Full call chain touched: execute_action signature gains notification_port kwarg → execute_actions_bounded gains it → DispatchWorker._execute_action_immediate passes it → DispatchWorker._flush_action passes it. DispatchWorker.__init__ gains notification_port: NotificationPort as a CONSTRUCTOR PARAMETER (not self-constructed — keeps the worker testable with InMemoryNotificationBackend; consistent with how workflow_executor and event_fire_callback are already injected). The worker factory create_dispatch_worker (services/_dispatch_worker.py) gains notification_port parameter; the API lifespan passes PgNotificationBackend(pool); the CLI dispatch-run command constructs its own PgNotificationBackend(pool) (it already has the pool — one line); tests pass InMemoryNotificationBackend.

Verify: red-green — a subscription with NotifyAction through the real dispatch worker writes a notification_deliveries row; the NOTIFY trigger fires.

---

## Phase 5 — Self-subscriptions

After Phases 1.1 + 4.

### 5.1 — create_principal gains self_subscription_filter
The service function create_principal in services/_identity.py gains `notification_event_types: list[str] | None` as a keyword parameter. New inject set constant (e.g., _INJECT_PRINCIPAL_CREATE) includes {dispatch_backend, principal_storage, kind_registry, caller_principal} — UPDATE the capability registration in _registry.py from _INJECT_PRINCIPAL_STORAGE_AND_KIND_REGISTRY to the new constant. The exact new function signature: `create_principal(dispatch_backend, principal_storage, kind_registry, caller_principal, *, kind, external_ref, display_name=None, notification_event_types=None)`. Validation: required for kind=consumer and app-registered kinds (hard error if None for those), must be None for run/source/system (hard error if provided). FLOW: mint the principal (get back minted_principal with DB-generated id), then internally construct FilterPredicate(event_types=notification_event_types, principal_id=minted_principal.id) + NotifyAction(target_principal_id=minted_principal.id, source_ref="self-subscription") + call dispatch_backend.create_subscription(filter_pred, [SubscriptionAction(action=notify_action)], principal_id=minted_principal.id) — the existing method on both dispatch backends. The caller passes ONLY the event types list — never touches FilterPredicate, NotifyAction, or principal ids. NOTE: run/source/system sites call mint_principal directly (not create_principal), so they inherently skip self-subscription logic — this is correct behavior (those kinds MUST NOT get self-subscriptions). CreatePrincipalParams gains notification_event_types as an optional list[str] field (simple, not a nested pydantic model).

Verify: consumer principal creation with filter → principal + subscription with NotifyAction created atomically; run/source/system creation with None → principal only; consumer creation without filter → hard error; existing tests swept for the positional change.

### 5.2 — Call site sweep
Every site that creates principals: run minting in services/_run.py passes notification_event_types=None; source minting in services/_dispatch.py passes None; consumer minting in auth tests / api lifespan passes a caller-chosen event_types list (e.g., ["run_completed", "run_failed", "task_failed", "inbox_answered"] — the caller decides, no implicit default); system principal seeding in db init / api lifespan passes None. No implicit default event types.

Verify: all call sites updated; full suite green; a newly-created consumer principal has exactly one self-subscription.

---

## Phase 6 — Principal notifications SSE

After Phase 3.

### 6.1 — SSE stream function
In notification/src/orxtra/notification/_stream.py: replicates the incoming SSE catch-up pattern against notification_deliveries. LISTEN on the global orxtra_notifications channel (the Phase 3.2 trigger's channel) via the EventBus (DispatchContext.event_bus, which already exists); filter NOTIFY JSON payloads by target_principal_id matching the authenticated caller (same pattern as incoming/_stream.py: global channel, client-side filtering, no per-principal channels). Replay unacknowledged deliveries from Last-Event-ID cursor. Deduplicate the overlap window. Heartbeat 15s. The function takes pool + event_bus + principal_id + optional last_event_id.

Verify: SSE stream receives catch-up notifications then live ones with dedup; heartbeat on idle.

### 6.2 — Compositor mount + auth
GET /notifications/stream on the api compositor, auth-gated. The handler resolves the caller's principal from AuthContext via the resolver, streams their notifications via 6.1. The caller can only stream their own (derived from auth context → resolver → principal; SYSTEM tier can stream anyone's via a query parameter). REST fallback via Phase 3.4's capabilities already exists.

Verify: authenticated GET returns text/event-stream; different principals see different streams; unauthenticated rejected.

---

## Phase 7 — Worker endpoint

After Phases 1.2 + 2.

### 7.1 — WorkerRegistry in compositor lifespan
WorkerRegistry() instantiated in the api lifespan, stored in CompositorConfig or app state. The get_worker_bridge callback constructed as a synchronous closure over the registry (returns the bridge for a given root, or None if no worker registered). Passed to DispatchContext as get_worker_bridge.

Verify: the callback is available on the DispatchContext; it is synchronous.

### 7.2 — Real worker WebSocket handler
CRITICAL: /workers/connect is a native route on the root Router, NOT a mounted sub-app — the auth middleware does NOT wrap it (it only wraps sub-apps via _mount_sub_app). Fix: create a Router with @router.ws("/connect"), build an ASGI app from it via create_app(worker_router), mount at /workers wrapped with auth_middleware — matching the MCP/A2A/AG-UI pattern. Remove the existing placeholder at _compositor.py:116-119. Then: read AuthContext from scope["state"]["auth_context"], accept the WebSocket, receive the WorkerRegistration from the first message, populate WorkerInfo.consumer_id from auth_context.consumer_id (WorkerInfo.consumer_id type changed from str to UUID | None to match AuthContext), register in the WorkerRegistry, create BrainWorkerBridge(ws, worker_id), run the bridge's message loop until disconnect, unregister on disconnect. The NativeWorker already sends the Authorization header — zero client changes.

Verify: NativeWorker connects, authenticates, registers, heartbeats, unregisters on disconnect; unauthenticated → 4001; one-per-root enforcement; consumer_id populated from auth context.

### 7.3 — Tool routing in the scheduler
CLARIFICATION: `start_run_from_file` is the CAPABILITY FUNCTION (the one the dispatcher calls with positionally-injected args). `start_run` is the inner function it calls. Both need the new params.

`start_run_from_file`'s capability injects gain get_worker_bridge and run_manager. With _INJECT_ORDER = pool(0), dispatch_backend(1), principal_storage(2), kind_registry(3), notification_port(4), get_worker_bridge(5), run_manager(6), caller_principal(7), and start_run_from_file's inject set = {pool, principal_storage, get_worker_bridge, run_manager, caller_principal}, the EXACT new function signature is: `start_run_from_file(pool, principal_storage, get_worker_bridge, run_manager, caller_principal, *, intent, config_path)`. start_run_from_file forwards get_worker_bridge and run_manager to start_run as keyword args: `start_run(pool, principal_storage, caller_principal, intent, config, get_worker_bridge=get_worker_bridge, run_manager=run_manager)`. start_run passes get_worker_bridge to the Scheduler constructor (Scheduler.__init__ gains get_worker_bridge: Callable | None = None) and registers in run_manager before execute_workflow. ServicesActionExecutor.execute_workflow (services/_actions.py:64) calls start_run_from_file directly and passes None for both new positional args. In _build_agent_tools (synchronous context): if task.execution_target is set, call get_worker_bridge(root) to look up the worker; for each tool, should_route_to_worker(tool_entry, task) determines the pipeline; ANYWHERE tools get wrapped via wrap_tool_for_remote using the bridge; LOCAL tools stay local; missing worker = hard error. Also: create_dispatch_worker (services/_dispatch_worker.py) gains a notification_port: NotificationPort parameter; the API lifespan passes PgNotificationBackend(pool); the CLI dispatch-run command (cli/src/orxtra/cli/_dispatch.py:74) constructs its own PgNotificationBackend(pool); tests pass InMemoryNotificationBackend. In _build_agent_tools (synchronous context — confirmed compatible with the synchronous callback): if task.execution_target is set, call get_worker_bridge(root) to look up the worker; for each tool, resolve_tool_location(tool_entry, task) determines the pipeline; ANYWHERE tools get wrapped via wrap_tool_for_remote using the bridge; LOCAL tools stay local; missing worker = hard error.

Verify: red-green — task with execution_target routes ANYWHERE tools through the bridge; LOCAL stays local; missing worker hard-errors.

---

## Phase 8 — RunManager + AG-UI live streaming

After Phase 0.2 (DispatchContext.run_manager field). Phase 7.1 must land before Phase 8.3 (both touch the compositor lifespan/config).

### 8.1 — Session + Scheduler sink management
Session gains add_sink(sink) and remove_sink(sink) (list append/remove; safe iteration via `for sink in list(self._sinks)` snapshot copy before iterating in _dispatch_to_sinks — prevents RuntimeError from list mutation during iteration, not async dispatch safety). Scheduler gains add_overseer_sink/remove_overseer_sink (same snapshot-copy pattern in _dispatch_to_overseer_sinks), plus add_transport_sink/remove_transport_sink. CRITICAL (a prior critique caught a double-add bug): new sessions receive `list(self._transport_sinks)` — a COPY, not a shared reference (Session.__init__ stores the passed list by reference; sharing + explicit add_sink = every sink dispatched twice). In `_agent_execution.py:_create_agent_session`, pass `sinks=list(self._transport_sinks)` to `create_session()`. Propagation to active sessions is exclusively via session.add_sink/remove_sink (the Scheduler iterates _task_sessions on add/remove). New sessions get a snapshot of current sinks at creation time.

Verify: a sink added after session creation receives subsequent events; a sink removed stops receiving; a new session created after a transport sink was registered receives it automatically; safe under concurrent dispatch (snapshot copy).

### 8.2 — RunManager class
Instance-scoped, held on DispatchContext (constructed in api lifespan). API: register_run(run_id, scheduler), deregister_run(run_id), subscribe(run_id, transport_sink, overseer_sink) → unsubscribe_callback | None. subscribe looks up the scheduler, calls add methods, returns a cleanup closure. Returns None if the run is not active. start_run's capability injects gain run_manager; the service function registers the scheduler in the RunManager BEFORE execute_workflow (critical — start_run blocks during execution; the RunManager must be populated before any SSE client could connect), deregisters in the finally block. Direct callers (start_run_from_file, ServicesActionExecutor) pass run_manager=None.

Verify: a running scheduler is findable by run_id; after completion it is gone; subscribe during a live run returns a working unsubscribe handle; subscribe after completion returns None.

### 8.3 — subscribe_run callback + AG-UI wiring
The compositor builds a subscribe_run callback from the RunManager: given (run_uuid: UUID, transport_sink, overseer_sink), calls run_manager.subscribe(...), returns the unsubscribe handle (or None). Note: the AG-UI handler must pass `run_uuid` (the UUID parsed at line 172 of _server.py), NOT the raw string `run_id`. Passed to create_agui_router. The AG-UI server's events_handler calls subscribe_run when a client connects; captures the returned unsubscribe handle in a local variable and wires it into the _wrapped_generator's finally block alongside registry.unsubscribe (the variable is captured via closure — the assignment and the finally block are in different scopes). Multiple concurrent clients get independent AGUITranslator instances (per-connection framing state).

Verify: SSE client connecting to an active run receives live transport + overseer events; disconnect cleans up sinks; two concurrent clients get independent event sequences.

### 8.4 — Enriched StateSnapshotEvent for late-joiners + completed runs
When an SSE client connects: the handler always sends a StateSnapshotEvent as the first event. For active runs: built from StateManager using current trace state, followed by live events. For completed runs (subscribe returns None): the snapshot is enriched with terminal metadata from read_run_report() — using EXISTING fields only (no schema additions): status (completed/failed/aborted — serves as the terminal indicator), finished_at, coherence_summary, total_cost_usd, per-task summaries (TaskSummary: status, attempt_count). No `error` field (status IS the error signal; coherence_summary provides context). No `final_verdicts` (TaskSummary.status is sufficient). After the snapshot, RunFinishedEvent (or RunErrorEvent for failures). Stream closes. No trace replay, no second translation path.

Verify: late-joiner to active run gets snapshot then live events; client to completed run gets enriched snapshot + terminal event only; the snapshot contains task statuses, decisions, cost.

---

## Phase 9 — E2E proof + migration

### 9.1 — Migration delta
notification_deliveries table delta against the v0.10.1 baseline (CREATE TABLE + FKs + indexes + trigger function + trigger). Harness: apply delta, assert table/constraints/trigger exist.

Verify: both baseline paths green (v0.8.0 chain + v0.10.1 chain).

### 9.2 — Notification delivery E2E
Consumer principal with self-subscription → fire a matching event → dispatch worker processes → NotifyAction writes delivery → principal SSE stream delivers it within seconds → ack clears it. Full cycle against real PG.

Verify: delivery arrives on SSE; ack clears it from unacknowledged list.

### 9.3 — Worker E2E
NativeWorker connects, authenticates, registers. Task with execution_target dispatched. ANYWHERE tools route through the bridge; LOCAL stays local. Results return. Worker disconnects → unregistered → subsequent targeted task hard-errors.

Verify: round-trip with remote tool results; disconnect → hard error.

### 9.4 — AG-UI E2E
Start a run; SSE client subscribes mid-run; receives translated events. Second client connects independently. Run completes; both streams end. Client connecting after completion gets enriched snapshot only.

Verify: live events arrive; two clients independent; post-completion = snapshot-only.

### 9.5 — In-memory parity sweep
All new protocol methods (NotificationPort, FilterPredicate.principal_id matching, ToolLocation resolver) parity-tested across PG and in-memory backends via shared parametrized test suites.

Verify: parity suite green.

---

## Phase 10 — Docs, changelog, release

### 10.1 — Documentation
CLAUDE.md/README templates: notification delivery model (subscriptions as universal routing, NotifyAction, self-subscriptions-as-preferences, NotificationPort protocol, global NOTIFY channel with client-side filtering), ToolLocation routing (LOCAL/ANYWHERE, should_route_to_worker composition, ToolCapability enum), worker lifecycle (connect → auth at WebSocket handshake → register → execute → disconnect), AG-UI live streaming (subscribe_run seam, runtime sinks with snapshot-copy safe iteration, enriched state snapshot for catch-up, snapshot-only for completed runs). selfdoc gen.

Verify: check_layer_docs green; selfdoc check green.

### 10.2 — Changelog + release
Breaking: TaskSpec.remote → execution_target, WebSocket auth required, FilterPredicate.principal_id new field, create_principal gains required self_subscription_filter with changed positional signature, WorkerRegistration capabilities typed as ToolCapability enum. Features: NotifyAction + notification deliveries, self-subscriptions, principal notifications SSE, worker endpoint live, ToolLocation routing, AG-UI live streaming + enriched snapshots. Release: RLSBL_PUSH_TIMEOUT=300 rlsbl monorepo release run --no-allow-dirty --watch --yes.

Verify: publish gate observed gating; CI router green; PyPI post-gate.

---

## Dependency structure

```
0.1, 0.2 (sequential — 0.2 is the merged infrastructure pass)
  → {1.1, 1.2, 2, 3.1+3.2} all parallel
    → {3.3+3.4, 4.1+4.2} (after 3)
      → 5 (after 1.1 + 4)
    → 6 (after 3)
    → 7 (after 1.2 + 2)
  → 8 (after 0.2; 8.3 depends on 7.1 for compositor wiring)
→ 9 (after everything)
→ 10
```

Parallelizable waves:
- Wave A: 0.1 then 0.2 (sequential — 0.2 is the merged infrastructure pass touching VALID_INJECT_TOKENS, _INJECT_ORDER, _INJECT_LABELS, DispatchContext, protocols)
- Wave B: 1.1, 1.2, 2.1+2.2, 3.1+3.2
- Wave C: 3.3+3.4, 4.1+4.2
- Wave D: 5.1+5.2, 6.1+6.2, 7.1+7.2+7.3
- Wave E: 8.1+8.2+8.3+8.4
- Wave F: 9.x
- Wave G: 10.x

## Out of scope (documented, deferred)

- Event-type registry with traits (the event types are free-form strings today; a structured registry is the correct long-term direction but is its own design round)
- Unified event ledger / event sourcing (replacing the trace events table with a fine-grained per-run ledger — the rung-10 solution for both AG-UI and delivery; explicitly deferred as a schema-replacement project)
- Per-run event channel replacing Scheduler sinks with pub/sub (the rung-10 run-registry solution; correct direction, separate round)
- Per-principal notification channels (the global NOTIFY channel with client-side filtering follows the incoming SSE precedent and scales adequately for single-operator)
- Multi-tenant / per-object authorization (single-operator model stands)
- Notification delivery channels beyond in-app (email, webhook, push — future action types in the same subscription chain)
