# Unified event bus and pub/sub subscription layer

## Context

An external product needs to share orxtra's event system rather than maintaining its own parallel event table and NOTIFY channel. The external system has a simpler event model (`event_type`, `payload`, `source`, `created_at`) while orxtra's events are scoped to runs and tasks. Both use asyncpg, both use PG LISTEN/NOTIFY, both enforce append-only immutability.

Rather than bridging two event systems, the proposal is for orxtra's trace module to become the canonical event store that external systems write to directly. On top of that, a pub/sub subscription layer would let users subscribe to event patterns and trigger composable actions.

## Part 1: Unified event table

### Schema changes to `events` table

- `run_id` becomes nullable (currently `NOT NULL REFERENCES runs(id)`). External events have no run.
- Add `source` column (text, nullable, indexed). External events set this to identify their origin (e.g., `"github"`, `"figma"`, `"ci"`). orxtra events can set it to `"orxtra"` or leave NULL.
- NOTIFY trigger payload gains `source` field in the JSON.

### Investigation already done

Every read query in the codebase filters by `WHERE run_id = $1`, which naturally excludes NULL rows. Every write provides run_id from its context. The NOTIFY trigger produces valid JSON with `"run_id": null`. No breakage found.

Files that touch the events table:
- `trace/src/orxtra/trace/_schema.py` — DDL, trigger, indexes
- `trace/src/orxtra/trace/_writer.py` — `write_event()`, `transition_run()`, `transition_task()`
- `trace/src/orxtra/trace/_reader.py` — `query_events()`
- `trace/src/orxtra/trace/_pg_backend.py` — delegates to reader
- `trace/src/orxtra/trace/_memory_backend.py` — in-memory filter
- `trace/src/orxtra/trace/_recovery.py` — crash recovery inserts
- `scheduler/src/orxtra/scheduler/_executor.py` — PG LISTEN handler

### New trace API surface

- `write_event()` must accept `run_id=None` and optional `source` parameter.
- A sync wrapper (like the external system's `fire_blocking()`) is needed: detects whether an event loop is running, dispatches to thread pool or uses `asyncio.run()`. External integration handlers call from sync code frequently.
- A `replay(event_types, since)` function for time-range queries across all events (not scoped to a run). The external system uses this for SSE catch-up.

### Undecided

- Should orxtra events default `source` to `"orxtra"` or leave it NULL? Pro of always setting it: uniform filtering. Pro of NULL: backward compatibility, no migration of existing data.
- Should the `source` column have a foreign key to a sources registry table, or stay as free-form text?
- Should `run_id`'s FK cascade behavior change? External events (NULL run_id) are unaffected by cascades, but the existing ON DELETE behavior for run-scoped events should be considered.
- Index strategy: the existing `idx_events_run_created` works for run-scoped queries. External events need an index on `(source, event_type, created_at DESC)` or similar. What composite index covers the pub/sub dispatch query pattern best?

## Part 2: Pub/sub subscription layer

### Concept

Users subscribe to event patterns and attach composable actions. No hardcoded notification types — actions are primitives that compose freely.

### Primitives

1. **Source** — a named ingest endpoint with owner, auth config, and slug. Writes events with `source = <slug>`, `run_id = NULL`.
2. **Subscription** — a user declares interest in events matching a filter expression.
3. **Action** — an atomic side-effect triggered when a subscription matches (send Slack DM, post to channel, fire outgoing webhook, push notification, trigger orxtra workflow, log).
4. **Accumulator** — optional wrapper on an action that buffers matched events and flushes on a schedule (daily/weekly digest) or count threshold.

### Rough data model

```
sources:
  id, owner_user_id, name, slug (unique), auth_method, auth_config (jsonb), created_at

subscriptions:
  id, user_id, source_id (nullable — NULL means "all events"), filter_expr, enabled, created_at

subscription_actions:
  id, subscription_id, position, action_type, action_config (jsonb), accumulator_config (jsonb, nullable)

accumulator_buffer:
  id, subscription_action_id, event_id (fk to events), created_at
```

### Dispatch flow

1. Event INSERT fires NOTIFY trigger
2. Dispatch listener (new LISTEN loop alongside scheduler's existing one) fetches full event
3. Queries subscriptions where filter matches
4. For each matching subscription, for each action:
   - No accumulator: execute immediately
   - Accumulator: buffer the event, skip execution
5. Periodic flush task evaluates accumulator conditions, executes actions with batched events

### Undecided

- **Filter expression language.** Options:
  - Structured key-value matching (`{event_type: "deploy.failed", source: "ci"}`) with exact and prefix match. Simple, indexable, covers most cases.
  - JSONPath predicates (`$.data.severity == "critical"`). More powerful, PG has `jsonb_path_exists`, harder to index.
  - SQL WHERE fragments. Maximum power, maximum risk.
  - Start simple and add expressiveness later? Or design for the powerful case upfront?

- **Where does the dispatcher live?** Options:
  - New orxtra module (orxtra owns dispatch, external systems register action implementations). Consistent with orxtra's tool registry pattern.
  - External system owns dispatch (reads from orxtra's event table, manages its own subscriptions). Keeps orxtra focused on orchestration.
  - Shared library extracted from both.

- **Action type registry.** How are new action types added? A plugin interface? A registry of callables? Does orxtra define the interface and external systems register implementations, or does each system bring its own action types?

- **Cycle detection for `trigger_workflow` action.** A workflow event can trigger another workflow. Options: depth counter on events, global per-source rate limit, explicit opt-in for recursive triggers, or just document the risk.

- **Accumulator flush mechanism.** PG-level scheduled task (`pg_cron`)? Application-level periodic task? A dedicated orxtra agent/service?

- **Subscription scoping.** Can users subscribe to orxtra-internal events (task transitions, run completions)? Or only to external-source events? Opening it up is more powerful but exposes internal state.

- **Multi-user visibility.** The concept includes per-endpoint "privy" users (users who can see a source's output and create subscriptions against it). Is this an ACL on the source, on the subscription, or on the events themselves?

- **Where do subscription/action configs live?** They reference user IDs, Slack channels, push tokens — these are product-level concepts. Does orxtra model users, or does it provide a generic `subscriber_id` that the external system maps to its user model?

## Effort estimate

- Part 1 (schema changes + API surface): Small-medium. Mostly additive — nullable column, new function signatures, sync wrapper, replay function. Migration for existing data.
- Part 2 (pub/sub layer): Medium-large. New tables, dispatch loop, action executor interface, accumulator flush, at minimum one action type implementation for testing. The undecided items above significantly affect scope.

Part 1 can ship independently and has value immediately (external system can write to and read from orxtra's event table). Part 2 builds on top.
