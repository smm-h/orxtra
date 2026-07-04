# Principals: unified identity model for ownership across orxtra

## Context

orxtra's dispatch system has subscriptions with `owner_run_id UUID REFERENCES runs(id)` — ties subscription ownership to orchestration runs. But external consumers need to own subscriptions too (e.g., end users, API clients, system processes). The current model can't express "user X owns subscription Y" without semantic abuse or shadow mapping tables.

Meanwhile, the auth module already has `consumers` (entities that hold credentials). These are a form of identity, but scoped narrowly to API authentication. Runs are another form of identity (they own subscriptions, produce events, hold advisory locks). These are all principals — entities that act within the system.

## Proposal

Introduce a first-class `principals` table in orxtra's auth module as the unified identity concept.

### Schema

```sql
CREATE TABLE principals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v7(),
    kind TEXT NOT NULL,           -- "run", "user", "consumer", "system", etc.
    external_ref UUID NOT NULL,   -- ID in the kind's namespace
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, external_ref)
);
```

### How it integrates

- **Subscriptions:** Replace `owner_run_id UUID REFERENCES runs(id)` with `principal_id UUID REFERENCES principals(id) ON DELETE CASCADE`. Filtering: `list_subscriptions(principal_id=...)`.
- **Runs:** When a run starts, register it as a principal with `kind='run'`, `external_ref=run_id`. Existing run-owned subscriptions migrate to the principal model.
- **Auth consumers:** Existing `consumers` become a principal kind (`kind='consumer'`). Whether consumers are migrated into the principals table or kept separate with a `principal_id` FK is a design choice.
- **External users:** A consuming app registers its users as principals with `kind='user'` (or a qualified kind like `kind='veliu_user'`). The consuming app stores `principal_id` on its user record for bidirectional FK integrity.
- **Sources:** Could gain `principal_id` for ownership (who created this source), though this is optional.
- **Events:** Could gain `principal_id` for attribution (who caused this event), replacing the free-text `source` for user-attributed actions.

### Migration path

1. Create `principals` table
2. Add `principal_id` column to `subscriptions` (nullable initially, alongside `owner_run_id`)
3. Backfill: for each subscription with `owner_run_id`, create a principal `(kind='run', external_ref=owner_run_id)` and set `principal_id`
4. Drop `owner_run_id` column (breaking change)
5. Update `Subscription` model: remove `owner_run_id`, add `principal_id: UUID | None`
6. Update `SubscriptionStorage` protocol: `list_subscriptions(principal_id=...)` filter

### Lifecycle

- CASCADE on `principal_id` FK means deleting a principal deletes all their subscriptions. This is the correct behavior: if a user is removed, their subscriptions should not survive as orphans.
- Creating a principal is the consuming app's responsibility. orxtra provides the CRUD (`create_principal`, `get_principal`, `delete_principal`).
- Principal deletion is a hard operation — cascades subscriptions. Document this clearly. Consuming apps should disable/deactivate rather than delete if they want soft-delete semantics.

## Undecided

- **Should `consumers` merge into `principals`?** Option A: consumers become a kind of principal (add `principal_id` FK to consumers, or migrate consumers into principals entirely). Option B: keep them separate — consumers are auth-specific (hold credentials), principals are ownership-specific (own things). Option C: consumers are replaced by principals + credentials reference principal_id directly.

- **Should `runs` get a `principal_id`?** If yes, runs register as principals automatically at start. This lets run-owned subscriptions work through the same mechanism. If no, run-owned subscriptions are expressed differently (or runs just pass `kind='run'` at subscription creation time without a principals row).

- **What goes in `kind`?** Open string, or a registered enum? Open string is flexible but typo-prone. Enum requires orxtra to know all kinds upfront (violates the "framework doesn't know about consumers" principle). A registered-at-startup approach (consuming apps declare their kinds) is a middle ground.

- **`display_name` — who sets it?** The consuming app at registration time? Or is it resolved dynamically (which would require an `OwnerResolver`-style protocol, adding complexity)?

- **Should principals appear in the capability registry?** I.e., should `create_principal`, `list_principals`, etc. be service-layer capabilities exposed via MCP/CLI/A2A?

- **Scope creep guardrail.** Principals should NOT grow into a user table (no email, no avatar, no preferences, no sessions). It's a foreign key target and a display name. Period. How to enforce this? A comment? A lint? A protocol that limits the fields?

## Effort estimate

Large. Touches: schema (new table + subscriptions migration), protocols (`SubscriptionStorage`, possibly `AuthStorage`), models (`Subscription`, new `Principal`), backends (`PgDispatchBackend`, `InMemoryDispatchBackend`), services (new principal CRUD, subscription filtering), and potentially the capability registry. Breaking change for `owner_run_id` consumers.

## Why this over simpler approaches

Simpler approaches (mapping tables, generic owner_id columns, JSONB metadata) all have the same problem: no FK integrity, no cascade, no unified concept. They punt the identity question to each consumer independently, creating N different ownership patterns. The principals approach answers it once, at the framework level, for all current and future consumers.
