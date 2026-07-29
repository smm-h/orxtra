---
title: Security Model
description: Authentication, authorization, principal-based attribution, trust tiers, credential verification, scope enforcement, and webhook HMAC verification.
---

# Security Model

orxtra enforces authentication, authorization, and attribution through three collaborating modules:

- **auth** -- credential verification, scope checking, ASGI middleware
- **identity** -- durable actor identity (principals table)
- **protocols** -- shared types (AuthContext, TrustTier, ConsumerRecord, Capability, scopes)

Every mutating API call flows through a single dispatch choke point that requires an authenticated caller and checks scopes before any work begins.


## Authentication

### Consumers

A **consumer** is a registered API client stored in the `consumers` table. Each consumer has:

| Field | Purpose |
|---|---|
| `id` | Primary key (UUIDv7) |
| `principal_id` | FK to the principals table (the consumer's durable identity) |
| `name` | Human-readable name (non-empty, CHECK enforced) |
| `trust_tier` | One of `anonymous`, `identified`, `verified`, `system` |
| `scope_grants` | JSONB array of granted scope strings |
| `disabled_at` | Non-null means the consumer is disabled; authentication fails immediately |

Consumer creation follows the **identity-at-birth** pattern: the caller mints a principal (kind=consumer, external_ref=consumer_id) first, then creates the consumer row that FKs to it. The principal exists before the consumer, so there is never a half-created actor.

### Credentials

Credentials are stored in the `credentials` table, linked to consumers via `consumer_id` (CASCADE on delete). Three credential types exist, enforced by a PostgreSQL enum:

- **`api_key`** -- a raw token hashed with SHA-256 at rest. Verified by comparing the hash of the presented token against the stored hash.
- **`bearer`** -- identical to api_key in storage and verification. The distinction is semantic (bearer tokens may come from an external issuer).
- **`hmac`** -- verified via a `KeyedMacProvider` (KMS-modeled protocol). The credential record stores a `secret_ref` pointing to a key in the secret registry. Raw key material never enters the auth module.

Credential records carry:

| Field | Purpose |
|---|---|
| `credential_hash` | SHA-256 hash of the raw credential value |
| `algorithm` | Hash algorithm (default: `sha256`) |
| `secret_ref` | Reference to a secret for HMAC verification (null for hash-based types) |
| `metadata` | Additional metadata (JSONB) |

### Authenticator

The `Authenticator` class dispatches verification to per-type verifiers:

- `HashCredentialVerifier` handles `api_key` and `bearer` types. It hashes the presented credential, looks up the matching `CredentialRecord` by hash, verifies the consumer is not disabled, and builds an `AuthContext`.
- `HmacCredentialVerifier` handles `hmac` types. It parses the presented credential as `identifier:signature:message`, delegates to the injected `KeyedMacProvider.verify()`, checks the `MacVerdict` outcome, and builds an `AuthContext`.

Every verification attempt (success or failure) emits an `AuthAuditEvent` via an optional `EventSink`. Audit events record the credential ID, type, consumer ID, outcome, and timestamp.

The authenticator has two entry points:

- `authenticate(raw_credential)` -- looks up the credential by hashing the raw value. Used by the ASGI middleware for standard API requests.
- `verify_by_credential_id(credential_id, presented_credential)` -- looks up by known credential ID. Used by the webhook receiver where the credential ID comes from the source record.

### AuthContext

On successful verification, the authenticator produces an ephemeral `AuthContext`:

```python
@dataclass(frozen=True)
class AuthContext:
    id: UUID                       # credential ID
    consumer_id: UUID | None       # None only for SYSTEM tier
    scopes: frozenset[str]         # from consumer's scope_grants
    trust_tier: TrustTier          # from consumer's trust_tier
    authenticated_via: str         # credential type ("api_key", "bearer", "hmac", "cli-local")
    issued_at: datetime
    expires_at: datetime | None
```

An AuthContext is never persisted. It exists for the duration of one request and carries the caller's identity and permissions into the dispatch layer.

### ASGI middleware

The `auth_middleware` function wraps an ASGI app and enforces authentication on every request:

- **HTTP requests**: extracts the bearer token from the `Authorization` header, calls `authenticator.authenticate()`, and attaches the resulting `AuthContext` to `scope["state"]["auth_context"]`. Returns 401 JSON on failure.
- **WebSocket connections**: same flow, but rejects with WebSocket close code 4001 (unauthorized) on failure. The inner app is never called for rejected connections.
- **Other scopes** (lifespan, etc.): passed through unchanged.


## Authorization

### Scopes

orxtra uses coarse scope strings for the single-operator model. Each scope controls access to a functional area:

| Scope | Controls |
|---|---|
| `runs:read` | Read run state, list runs |
| `runs:manage` | Start, abort, pause runs |
| `inbox:read` | Read inbox items |
| `inbox:respond` | Answer, skip, reject inbox items |
| `notifications:read` | List notification deliveries |
| `notifications:manage` | Acknowledge notification deliveries |
| `trace:read` | Query events, transcripts, tasks, decisions, constraints |
| `events:read` | Read events |
| `events:write` | Fire events |
| `config:read` | Read configuration |
| `validate:read` | Run validation checks |
| `sources:read` | List and inspect sources |
| `sources:manage` | Create and delete sources |
| `subscriptions:read` | List subscriptions |
| `subscriptions:manage` | Create and delete subscriptions |
| `principals:read` | List and inspect principals |
| `principals:manage` | Create and delete principals |

The full set is defined as `ALL_SCOPES` in `orxtra.protocols`.

### Capabilities and scope enforcement

Every dispatchable operation is registered as a `Capability` with a `required_scope`:

```python
@dataclass(frozen=True)
class Capability:
    name: str
    namespace: str
    required_scope: str        # e.g., "runs:manage"
    injects: frozenset[str]    # infrastructure dependencies
    # ... other fields
```

Authorization is enforced at the **single dispatch choke point** in `services._dispatcher.dispatch()`. Before any parameter validation or dependency injection:

1. If `context.auth_context` is None, dispatch raises `ValueError` -- an API served without an authenticator cannot dispatch capabilities.
2. The `Authorizer.authorize()` method checks that `required_scope` is in `auth_context.scopes`. If not, it raises `AuthorizationError`.

The `Authorizer` is stateless -- a single module-level instance enforces every dispatch. There is no bypass, no fallback, and no optional mode.

### The CLI path

The CLI is the local-trust path. It creates an operator `AuthContext` with `TrustTier.SYSTEM`, all scopes (`ALL_SCOPES`), and `consumer_id=None`. This context passes every scope check and resolves to the system principal. The CLI talks directly to the database with no HTTP layer.


## Principal-based attribution

### Principals

A **Principal** is a durable identity row in the `principals` table. Every actor in the system -- a run, an API consumer, a webhook source, the system itself, or an app-registered kind -- gets exactly one row.

```python
@dataclass(frozen=True)
class Principal:
    id: UUID
    kind: str               # "run", "consumer", "source", "system", or app-registered
    external_ref: UUID      # the actor's id in its kind's namespace
    display_name: str | None
    created_at: datetime
```

The table has a `UNIQUE(kind, external_ref)` constraint, ensuring exactly one principal per actor.

Principals are **not** the per-request authentication context (that is `AuthContext`). A Principal is the stable, persisted identity that an `AuthContext` resolves to across many requests.

### Kinds

Four built-in kinds are owned by the framework:

| Kind | Represents |
|---|---|
| `run` | An autonomous workflow run |
| `consumer` | An API client |
| `source` | A webhook event source |
| `system` | The singleton system identity |

Apps may register additional kinds (e.g., `user`) via `ServerConfig.principal_kinds`. A `KindRegistry` validates kinds at the service layer -- an unknown kind is a hard error. The registry is instance-scoped (constructed per composition root, not module-global).

### Trust tiers

The `TrustTier` enum defines four levels:

| Tier | Meaning |
|---|---|
| `anonymous` | Unauthenticated or minimally identified |
| `identified` | Credential verified, identity known |
| `verified` | Credential verified with additional assurance |
| `system` | Framework-internal, highest privilege |

Trust tiers are stored on the consumer record and propagated to the `AuthContext` on authentication.

### Identity at birth

Runs, sources, and consumers mint their principal at creation, transactionally adjacent (mint-first, idempotent). The `mint_principal()` storage method is an idempotent upsert on `(kind, external_ref)`: insert if absent, return existing if present. A principal orphaned by a crashed creation is harmless and swept by recovery (age-guarded via `sweep_orphaned_run_principals`).

### Caller resolution

The `resolve_caller_principal()` function maps an ephemeral `AuthContext` to its persisted `Principal`:

- **SYSTEM tier**: resolves to the singleton system principal (fetched by kind=system and the all-zeros UUID external_ref). If absent, the database was never seeded -- hard error.
- **Non-SYSTEM with no `consumer_id`**: invalid -- only SYSTEM-tier contexts may omit consumer identity. Hard error.
- **Non-SYSTEM with `consumer_id`**: resolves via kind=consumer and the consumer's UUID. A consumer without a backing principal is an integrity violation -- hard error.

### Attribution vs ownership

Foreign keys to principals follow two patterns:

- **History-bearing (RESTRICT)**: `events.principal_id`, `runs.created_by`, `sources.created_by`, `inbox_items.resolved_by`, `consumers.principal_id`. An actor with history is undeletable (raises `PrincipalInUseError`). A principal that has never acted deletes cleanly.
- **Operational state (CASCADE)**: `subscriptions.principal_id`. Deleting the owning principal takes its subscriptions with it.

Every event carries a NOT NULL `principal_id` naming the actor that emitted it. The principal is determined by context:

- The **run principal** for scheduler/overseer paths
- The **consumer principal** for API callers
- The **source principal** for webhooks
- The **subscription owner** for dispatch-derived events
- The **system principal** for internal machinery


## SYSTEM-tier behavior

The SYSTEM tier is the highest-privilege context, reserved for framework-internal operations and the local CLI operator.

### Properties

- `consumer_id` is None -- SYSTEM-tier contexts have no backing consumer record.
- `scopes` contains ALL_SCOPES -- every scope check passes.
- Resolves to the **singleton system principal** (kind=system, external_ref=all-zeros UUID).
- The system principal is seeded once during `orxtra db init` and is never deletable (the service layer hard-errors on any attempt).

### Where SYSTEM tier is used

- **CLI**: the local operator's `AuthContext` is SYSTEM-tier with all scopes, authenticated via `cli-local`.
- **Action execution**: workflow actions and event actions resolve the system principal for attribution when firing events or starting workflows from dispatch subscriptions.
- **Notification streaming**: SYSTEM-tier callers may pass `?principal_id=<uuid>` to stream another principal's notifications. Non-SYSTEM callers that pass a different principal_id receive 403.


## Webhook HMAC verification

External event sources send webhooks to `POST /events/{slug}`. The incoming module verifies each request before accepting it.

### Verification flow

1. **Source lookup**: the slug identifies a registered `Source` record. The source carries a `credential_id` pointing to the credential used for verification. Sources without a credential are rejected (403).

2. **Credential extraction**: the receiver inspects the source config to determine the authentication method:
   - If `signature_header` is set in the source config, the request is HMAC-authenticated. The signature is extracted from the named header, common prefixes are stripped (e.g., `sha256=` from GitHub webhooks), and the presented credential is formatted as `_:signature:body` for the HMAC verifier.
   - Otherwise, the token is extracted from the `Authorization` header (or a custom header via `auth_header` in the source config) for bearer/api_key verification.

3. **Credential verification**: `authenticator.verify_by_credential_id()` looks up the credential by its known ID (from the source record, not by hash) and delegates to the appropriate verifier.

4. **Attribution**: the verified `AuthContext` identifies the presenting consumer (logged for audit). However, the fired event is attributed to the **source principal** -- the durable identity of the webhook endpoint -- not the presenting consumer. The source principal is resolved via `principal_storage.get_principal_by_ref(KIND_SOURCE, source.id)`.

5. **Event firing**: the event is fired with the source principal, `run_id=None` (external events are not tied to a run), and an optional idempotency key extracted from a configurable header.

### HMAC key management

HMAC credentials use a `secret_ref` on the credential record that references a key in the secret registry. The `HmacCredentialVerifier` delegates to an injected `KeyedMacProvider` for verification. The provider follows a KMS model:

- The only operation is `verify()` -- there is no get-value or resolve method.
- Key export is impossible by construction.
- Multiple concurrently-valid key versions enable rotation.
- Verdicts report the matched version via `MacVerdict.matched_version`.

The `MacVerdict` result carries:

| Field | Purpose |
|---|---|
| `outcome` | `match` or `mismatch` |
| `secret_name` | Which secret was used |
| `algorithm` | Hash algorithm |
| `verified_at` | Timestamp |
| `matched_version` | Which key version matched (for rotation) |

### Source configuration

Source config fields that control webhook verification:

| Field | Purpose |
|---|---|
| `signature_header` | Header name containing the HMAC signature (e.g., `X-Hub-Signature-256` for GitHub). Presence selects the HMAC path. |
| `auth_header` | Custom header for bearer/api_key auth (default: `Authorization`) |
| `event_type_source` | Where to find the event type: `header`, `json_field`, or `constant` |
| `event_type_field` | The header name, JSON field path (dot-separated for nesting), or constant value |
| `idempotency_header` | Header name for the idempotency key (optional) |
