# Delivery + Worker + AG-UI — post-implementation follow-ups

## Context

The delivery + worker + AG-UI live-streaming plan (`todo/.done/delivery-worker-agui-plan.md`
once moved) shipped in orxtra 0.12.0 (current version 0.13.0). A five-auditor
review (git-history-blind, working-tree-as-truth) verified all 11 phases are
implemented and released, ~1005+ tests passing across real-PG suites, with every
adversarial failure-mode check (wired-but-unread field, missing auth wrapper,
double-add sink bug, silent serialization gap, register-before-execute ordering,
no-invented-schema) holding.

This todo collects everything the audit found still open. Four items carry design
decisions that were resolved (recorded below); two of those carry sub-decisions
that must be nailed down before an executable plan exists. The rest are mechanical.

Decision cadence (resolved): all of this ships as ONE batched fix release, not
per-item and not deferred to ride an unrelated release.

---

## A. Active-run AG-UI snapshot not enriched (real functional gap)

**Finding (Phase 8.4).** Mid-run SSE joiners receive `{"run_id": run_id}` only and
are blind to everything that happened before they connected. The completed-run
path IS correctly enriched (from `read_run_report`, existing fields only). The
active-run path was specified to send a StateManager-built snapshot (task
statuses / decisions / cost) before live events, but that enrichment — which
exists and is unit-tested (`agui/tests/test_agui.py` around the StateManager
snapshot case) — is NOT wired into the live branch of `agui/src/orxtra/agui/_server.py`
(the minimal `{"run_id": ...}` is sent at roughly `_server.py:261`, enrichment
only inside the `if not is_live` branch).

**Consequence.** The shipped `CLAUDE.md` claims "StateSnapshotEvent is enriched
for late-joiners" — currently false for the live path. This is the closest thing
to a shipped defect in the whole feature (docs overclaim behavior).

**Decision (resolved): wire the enriched snapshot.** Honor the original plan: a
mid-run joiner gets a StateManager snapshot at connect, then live events.

**Sub-decision still open (must resolve before planning): the snapshot/live race.**
The implementer may have skipped wiring precisely because of this. An event can
fire between snapshot-build and subscription-start, causing a double-count or a
gap. Candidate shape: subscribe first (start buffering live events), then build
the snapshot, then drain the buffer with dedup against what the snapshot already
reflects. The exact contract needs grounding against how `subscribe_run`, the
per-run broadcaster (`agui/_registry.py`), and `StateManager` actually interact —
specifically what sequence point the snapshot is taken at vs the first buffered
live event, and how dedup keys events. Resolve this (likely a short ASKME after
investigation) before the plan is executable.

**Also:** add the true AG-UI E2E the audit found missing (Phase 9.4 is seam-level
only) — a real run + HTTP SSE client subscribing mid-run receiving live translated
events + a second independent client + completion ending both streams + a
post-completion client getting enriched-snapshot-only. This E2E is what proves the
wired snapshot works; it follows directly from this item.

**Files:** `agui/src/orxtra/agui/_server.py`, `agui/src/orxtra/agui/_registry.py`,
StateManager (`agui/src/orxtra/agui/_state.py` or equivalent), a new
`tests/test_e2e_agui_live.py` HTTP-level test.

---

## B. ToolCapability is unconsumed by routing (design gap)

**Finding (Phase 2.1).** The entire `ToolCapability` enum (READ/WRITE/EXEC/HTTP/GIT,
`protocols/_types/_enums.py`) is defined but never consumed by worker routing —
routing keys off `ToolLocation` only. `ToolCapability.EXEC` is orphaned (assigned
to no tool anywhere; there is no first-class exec tool — command execution exists
only as inline `[[tools.define]]` CommandExecution tools, built with default
ANYWHERE + empty capabilities). The original routing design intended capabilities
to filter which tools a worker can handle (a read-only worker rejects write tools).

**Decision (resolved): wire capability-matching.** Workers declare capabilities;
routing validates each ANYWHERE tool against the connected worker's declared set;
a mismatch is a hard error (no silent drop, no silent local fallback — consistent
with the no-silent-degradation rule). Assign EXEC to the inline command tools so
it becomes meaningful. Enables heterogeneous workers (e.g. a read-only CI worker).

**Sub-decisions still open (must resolve before planning):**
- **Enforcement point.** Per-tool validation at routing time (in the
  `_create_agent_session` routing block at `scheduler/_agent_execution.py`
  ~`:1053-1097`) vs at worker-registration time. Routing-time is the likely read
  of the decision (the worker set is known when a targeted task routes), but pin it.
- **Mismatch shape.** A targeted task needs a tool the connected worker can't
  handle → hard error naming the tool + missing capability + worker root. Confirm
  it is never a silent drop of the offending tool nor a silent local fallback.
- **EXEC assignment.** Tag the inline CommandExecution tools with EXEC (they are
  built via `build_command_tool` at `_agent_execution.py` ~`:1323`), since they are
  the only command-execution surface.

**Alternative that was rejected:** dropping `ToolCapability` as YAGNI (as REMOTE
was dropped from ToolLocation) — rejected in favor of wiring the matching.

**Files:** `protocols/_types/_enums.py`, `scheduler/_tool_registry.py`,
`scheduler/_agent_execution.py`, `worker/_pipeline_split.py`
(`should_route_to_worker` / routing), `worker/_protocol.py` (WorkerRegistration
capabilities), `worker/tests/`, a routing/capability E2E.

---

## C. acknowledge_delivery ownership cap (robustness bug)

**Finding (Phase 3.4).** `services/_notifications.py` `acknowledge_delivery`
establishes the caller's owned set via `list_for_principal(..., limit=1000)`; a
caller owning >1000 deliveries gets a false `PermissionError` on a genuinely-owned
delivery beyond that window.

**Decision (resolved): structural SQL scoping.** Change
`NotificationPort.acknowledge` to `acknowledge(delivery_id, principal_id)`: the
UPDATE is scoped `WHERE id = $1 AND target_principal_id = $2`, and 0 rows affected
means not-found-or-not-owned (single query, no window, ownership enforced
structurally — matching how the rest of the system scopes). This is a protocol
signature change touching both backends (`PgNotificationBackend`,
`InMemoryNotificationBackend`) and the `acknowledge_delivery` service caller.
Low ambiguity, but it is a contract change so it is recorded as a decision.

**Files:** `protocols/_contracts.py` (NotificationPort), `notification/_backend.py`,
`notification/_inmemory.py`, `services/_notifications.py`, `notification/tests/`.

---

## D. Mechanical cleanup (no decisions — do alongside the fixes)

- **selfdoc drift.** The repo is not selfdoc-clean: DRIFT001 on
  `cli-src-orxtra-cli-_db.md` plus 9 undocumented symbols in generated
  `services/src/orxtra/services/_generated/schema_executor.py`. These stem from
  post-0.13.0 Unreleased work (db init/verify, custom pricing), NOT the delivery/
  worker/AG-UI phases — but the batched release must clear them (run `selfdoc gen`;
  decide whether generated `_generated/*` symbols should be documented or excluded
  from coverage — a small recurring policy call for generated code).
- **Stale comment.** `api/src/orxtra/api/_compositor.py:351-352` falsely claims the
  auth middleware "passes non-HTTP scopes (websocket, lifespan) through untouched."
  The middleware authenticates WebSocket scopes and rejects unauthenticated ones
  with code 4001; only lifespan passes through. Correct the comment.
- **Placeholder stub.** The accept-then-close worker WS placeholder survives in the
  `worker_registry is None` branch (`_compositor.py:156-159`). Harmless in real
  deployments (the real handler is always used when a registry is configured), but
  the plan called for its removal — delete or justify the degenerate branch.
- **Todo hygiene.** Move `todo/delivery-worker-agui-plan.md` to `todo/.done/`
  (its work is implemented and released — verified by this audit).

---

## E. Spec-alignment deviations (ACCEPT — no code change; recorded for the record)

The auditors flagged these as deviations from the plan's literal wording; all are
improvements or correct adaptations to the real code, so accept them (the plan is
an immutable historical artifact — nothing to amend):

- `create_principal` omits the unused `caller_principal` inject (leaner; the
  self-subscription keys off the minted principal id, never the caller).
- The notification SSE function takes `notification_port` rather than a raw `pool`
  (cleaner abstraction).
- notification `depends_on = ["identity", "protocols"]` vs plan's `["protocols"]`
  (correct — FK target lives in identity).
- Generated schema lives at `services/src/orxtra/services/_generated/`, not
  `schema/_generated/` (plan's path label was wrong; content is correct).
- `should_route_to_worker(tool_location, execution_target)` and routing living in
  async `_create_agent_session` rather than sync `_build_agent_tools` — functionally
  equivalent, and `get_worker_bridge` is still invoked synchronously.

---

## Out of scope (deferred future rounds — NOT part of this todo)

Each is its own future design round, explicitly deferred by the original plan:
event-type registry with traits; unified event ledger / event sourcing (the rung-10
direction for both AG-UI and delivery — a schema-replacement project); per-run event
channel replacing scheduler sinks with pub/sub; per-principal notification channels
(the global NOTIFY channel with client-side filtering suffices for single-operator);
notification channels beyond in-app (email/webhook/push — future action types in the
same subscription chain); multi-tenant / per-object authorization (single-operator
model stands).

---

## Sequencing note (for whoever picks this up)

Per the planning-discipline rule, this is NOT yet an executable plan: items A and B
carry open sub-decisions (A's snapshot/live race contract; B's enforcement point,
mismatch shape, and EXEC assignment). The correct sequence is:
1. Ground A and B against the current code.
2. Resolve their sub-decisions (short ASKME round).
3. Build the batched plan (A + B + C + D), critique it, then implement.
4. Ship all of it as one batched fix release (decision D-cadence above), covering
   the selfdoc drift in the same release.

## Effort

Medium. C + D are small and mechanical. A and B are the substance — each a
focused feature slice with its own E2E, plus a shared release at the end.
