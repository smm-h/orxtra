# HTTP layer for external event ingestion

## Context

orxtra v0.6.0/v0.7.0 shipped a solid event bus: source CRUD, subscriptions with filters, typed actions, accumulator buffering, PG-backed persistence. But the entire system is only accessible via direct Python API calls (asyncpg pool required). There's no way for external systems to POST events over HTTP.

The `sources` table stores `auth_method` and `auth_config`, but nothing validates them. These fields are write-only — stored and never consulted.

For orxtra's event bus to be useful beyond internal orchestration, any consumer (not just the current one) needs a way to send events in over HTTP and have them validated, stored, and dispatched to subscriptions.

## What's missing

### 1. Webhook receiver endpoint

A generic HTTP endpoint that accepts events from external systems:

- `POST /events/{source_slug}` (or similar)
- Resolves the source by slug from the dispatch backend
- Validates the request (see auth below)
- Writes the event via `fire_event(run_id=None, source=slug, ...)`
- Returns 200 immediately (or 202 if processing is async)
- Returns 404 for unknown slugs, 401/403 for auth failures

### 2. Auth validation

The source model already has `auth_method` and `auth_config`. The receiver needs to actually enforce them:

- `hmac_sha256` — validate request body against a signature header using a shared secret from `auth_config`. Need to decide: which header? Configurable per source, or a fixed convention?
- `bearer` — validate `Authorization: Bearer <token>` against a token in `auth_config`
- `none` — accept anything (for testing, internal sources)
- Extensible: new auth methods shouldn't require changing the receiver code

### 3. Payload normalization

External webhooks have arbitrary JSON shapes. The receiver needs to decide how to map them to orxtra's event format:

- `event_type` — extracted from payload? From a header? From the URL? Configurable per source?
- `data` — the raw payload, a subset, or a transformed version?
- Simplest approach: `event_type` comes from a field in the payload (configurable via source config, e.g., `event_type_field: "action"`) or a header, with a fallback to `"{slug}.event"`. `data` is the raw payload.

### 4. Idempotency / deduplication

External systems may retry webhook deliveries. Without dedup, the same event gets stored and dispatched multiple times. Options:

- Idempotency key in a request header (e.g., `X-Idempotency-Key`) — receiver checks if already seen before writing
- Content-hash dedup within a time window
- Leave it to the consumer (document that events may duplicate)

### 5. Replay endpoint

A way to query historical events for a source:

- `GET /events/{source_slug}?since=<cursor>&limit=N`
- Uses the existing `replay()` function
- Useful for SSE catch-up, debugging, and external consumers that missed events

### 6. Event streaming endpoint

Real-time event delivery over SSE or WebSocket:

- `GET /events/{source_slug}/stream` (SSE)
- Uses the existing `event_stream()` async generator
- Supports `Last-Event-ID` for catch-up
- Optional filter by event_type

## Design considerations

### Where does the HTTP server live?

orxtra currently has no HTTP server — only CLI and MCP (stdio). Options:

- New `http` or `api` module with a lightweight ASGI app (e.g., Starlette) that can be mounted into a consumer's web server or run standalone
- Extend the MCP module to also serve HTTP (conflates two concerns)
- Provide the endpoint handlers as functions that consumers mount into their own web framework

### Auth extensibility

The auth validation should be a protocol/registry pattern, not a switch statement. Each auth method is a callable: `(request, auth_config) -> bool`. New methods register by name.

### Rate limiting

External sources may flood the event bus. Should there be per-source rate limiting? Per-source event count caps? Or is that the consumer's problem?

### Source ownership and access control

Sources currently have no owner. For multi-user scenarios:

- Who can create/delete sources?
- Who can fire events to a source? (Anyone with the auth credentials, or only the owner?)
- Who can subscribe to a source's events? (Anyone, or only users explicitly granted access?)

The current model treats sources as global shared resources. Multi-user access control may need an `owner_id` or ACL on sources.

## Effort estimate

- Webhook receiver with auth validation: medium (endpoint + auth protocol + integration with existing fire_event)
- Replay and streaming endpoints: small (wrappers around existing replay/event_stream)
- Idempotency: small-medium (key storage + lookup before write)
- HTTP server infrastructure: depends on approach (standalone ASGI app vs. mountable handlers)
