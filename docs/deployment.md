---
title: Deployment
description: Database setup, API server, dispatch worker, tool worker, environment variables, and production considerations.
---

# Deployment

orxtra has three long-running processes: the API server, the dispatch worker, and optionally one or more tool workers. All three require a PostgreSQL database.

## Prerequisites

- Python 3.12+
- PostgreSQL 15+ with the [pg_uuidv7](https://github.com/fboulnois/pg_uuidv7) extension (or use the built-in stub for development)
- [pgdesign](https://github.com/smm-h/pgdesign) binary on PATH (for migrations only)

Install orxtra:

```
uv pip install orxtra
```

## Database setup

### Create the database

Create a PostgreSQL database for orxtra. The connection URL follows the standard `postgresql://` format:

```
postgresql://user:password@host:5432/orxtra
```

### Initialize the schema

The `db init` command creates all tables, types, extensions, and indexes. It is idempotent -- safe to run repeatedly.

```
orxtra --db postgresql://user:password@host:5432/orxtra db init
```

This runs the pgdesign-generated schema executor, which creates objects across five schema files (identity, trace, dispatch, auth, notification) in dependency order. It also seeds the singleton system principal used for internal attribution.

**pg_uuidv7 extension**: The schema requires UUIDv7 generation. In production, install the `pg_uuidv7` PostgreSQL extension. For development or environments where installing extensions is not possible, use the `--use-extension-stub` flag, which substitutes `gen_random_uuid()`:

```
orxtra --db postgresql://... db init --use-extension-stub
```

The stub produces v4 UUIDs instead of time-ordered v7 UUIDs. This is acceptable for development but loses the time-ordering property in production.

### Verify the schema

Check that all required database objects exist:

```
orxtra --db postgresql://... db verify
```

Exits with code 0 if the schema is complete, code 1 with a list of missing objects otherwise.

### Migrations

For schema changes between versions, orxtra wraps pgdesign's migration system:

```
# Preview pending changes (no files generated)
orxtra --db postgresql://... db migrate plan

# Apply pending migrations
orxtra --db postgresql://... db migrate apply

# Show migration status
orxtra --db postgresql://... db migrate status

# Preview migration SQL without executing
orxtra --db postgresql://... db migrate apply --dry-run
```

## Running the API server

The `serve` command starts the HTTP compositor, which mounts all protocol servers on a single ASGI application backed by granian (via fastware):

```
orxtra --db postgresql://... serve --port 8080
```

### Required flags

- `--db`: PostgreSQL connection URL (global flag)
- `--port`: Port to listen on

### Optional flags

- `--host`: Bind address (default: `0.0.0.0`)
- `--secrets-env`: JSON object mapping secret names to environment variable names, used to construct the authentication stack

### Mounted endpoints

| Path | Protocol | Purpose |
|---|---|---|
| `/mcp` | MCP Streamable HTTP | Tool interface for AI clients |
| `/a2a` | A2A JSON-RPC | Agent-to-agent protocol |
| `/ag-ui/*` | AG-UI SSE | Frontend streaming for human UIs |
| `/notifications/stream` | SSE | Per-principal notification delivery |
| `/incoming/events/{slug}` | HTTP POST | Webhook ingestion (HMAC-verified) |
| `/workers/connect` | WebSocket | Brain-worker tool execution |
| `/.well-known/agent.json` | GET | A2A agent card |
| `/health` | GET | Health check |

### Startup sequence

1. Creates an asyncpg connection pool
2. Verifies the database schema (errors if incomplete)
3. Seeds the singleton system principal (idempotent)
4. Starts the PG event bus (LISTEN/NOTIFY)
5. Constructs the dispatch context, worker registry, and run manager
6. Optionally constructs the auth stack from `--secrets-env`
7. Optionally mounts the incoming webhook receiver (requires auth)
8. Mounts all protocol sub-apps (MCP, A2A, AG-UI, notifications, workers)

### Authentication

Without `--secrets-env`, sub-apps are mounted without authentication (explicit unauthenticated mode for local development). With `--secrets-env`, the full auth stack is constructed:

```
orxtra --db postgresql://... serve --port 8080 \
  --secrets-env '{"hmac_key": "ORXTRA_HMAC_KEY", "api_secret": "ORXTRA_API_SECRET"}'
```

The JSON maps secret names to environment variable names. The auth stack creates:

- `HashCredentialVerifier` for bearer tokens and API keys
- `HmacCredentialVerifier` for HMAC-signed requests (webhooks)
- `Authenticator` wrapping all verifiers
- Auth middleware applied to MCP, A2A, AG-UI, notification, and worker endpoints

### CORS

By default, no CORS middleware is applied (safe for local and reverse-proxied deployments). For browser-facing deployments, configure `cors_origins` via the Python API (`ServerConfig.cors_origins`). Wildcard `*` is rejected because fastware enables credentialed CORS.

### MCP transport security

By default, the MCP endpoint accepts connections only from loopback addresses (`localhost:*`, `127.0.0.1:*`, `[::1]:*`). For reverse-proxied deployments, add the proxy's Host header value via `ServerConfig.mcp_allowed_hosts` in the Python API.

## Running the dispatch worker

The dispatch worker processes persistent event subscriptions. It polls the events table (with LISTEN/NOTIFY acceleration) and executes subscription action chains:

```
orxtra --db postgresql://... dispatch run
```

### Optional flags

- `--cursor`: Cursor name for this worker instance (default: `main`). Multiple workers can run with different cursor names for parallel processing.
- `--poll-interval`: Fallback poll interval in seconds (default: `5.0`). LISTEN/NOTIFY provides near-instant waking; this is the backup.
- `--batch-size`: Maximum events per polling batch (default: `100`).

### Graceful shutdown

The dispatch worker handles SIGINT and SIGTERM for graceful shutdown. It finishes processing the current batch before exiting.

## Running tool workers

Tool workers connect to the API server over WebSocket and execute tool calls against a local filesystem. This enables remote tool execution -- the brain (scheduler) runs on the server, while the worker runs on the machine with the project files.

### Native worker

```
orxtra worker connect \
  --brain ws://server:8080/workers/connect \
  --root /path/to/project \
  --key YOUR_API_KEY
```

The native worker:

- Authenticates via Bearer token in the WebSocket handshake
- Registers with the brain, declaring its project root and capabilities
- Receives `ExecuteToolCall` messages and executes tools locally
- Returns `ToolCallResult` messages with output and mutation tracking
- Reconnects automatically on connection loss (exponential backoff: 1s to 60s)

Available tools on the worker: read, write, edit, multi_edit, grep, glob, stat, diff, list_dir, mkdir, move, copy, delete, set_executable, git (status/log/diff/show/blame/branches/changed_files/commit), pytest, uv.

### Docker worker

```
orxtra worker docker \
  --brain ws://server:8080/workers/connect \
  --image orxtra-worker:latest \
  --root /path/to/project \
  --key YOUR_API_KEY
```

The Docker worker launches a container with the project root volume-mounted at `/project` and passes connection details via environment variables (`ORXTRA_BRAIN_URL`, `ORXTRA_API_KEY`, `ORXTRA_ROOT`).

## Environment variables

orxtra does not read configuration from environment variables at runtime (no implicit defaults). All configuration is passed via CLI flags or the Python API. However, these environment variables are relevant:

| Variable | Used by | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Transport layer | Anthropic API authentication |
| `OPENAI_API_KEY` | Transport layer | OpenAI API authentication |
| `GOOGLE_API_KEY` | Transport layer | Google AI API authentication |
| `ORXTRA_BRAIN_URL` | Docker worker | Brain WebSocket URL (inside container) |
| `ORXTRA_API_KEY` | Docker worker | API key (inside container) |
| `ORXTRA_ROOT` | Docker worker | Project root path (inside container) |

LLM API keys must be set in the environment where the scheduler runs (the server process, not the worker). The `--secrets-env` flag on `serve` maps secret names to environment variable names for the auth stack only.

## Production considerations

### Process architecture

A production deployment runs three process types:

1. **API server** (`orxtra serve`): Handles HTTP requests from clients, MCP tools, A2A agents, and AG-UI frontends. Accepts WebSocket connections from tool workers.
2. **Dispatch worker** (`orxtra dispatch run`): Processes event subscriptions asynchronously. Run one or more instances with distinct `--cursor` names.
3. **Tool workers** (`orxtra worker connect`): Run on machines with project files. One worker per project root.

### Database

- Use a dedicated PostgreSQL database. The schema creates tables across identity, trace, dispatch, auth, and notification domains.
- Install the `pg_uuidv7` extension for time-ordered UUIDs (do not use the stub in production).
- The append-only event store (trace) grows indefinitely. Plan for storage accordingly.
- LISTEN/NOTIFY channels are used for real-time event delivery (`orxtra_events`, `orxtra_notifications`). Ensure the PG connection supports these.

### Reverse proxy

When deploying behind a reverse proxy (nginx, Caddy, etc.):

- Forward WebSocket connections for `/workers/connect`
- Forward SSE connections for `/ag-ui/*` and `/notifications/stream`
- Set appropriate timeouts for long-lived connections
- Configure `ServerConfig.mcp_allowed_hosts` with the proxy's Host header value
- Configure `ServerConfig.cors_origins` with browser-facing origins

### Schema verification

Every long-running process verifies the schema at startup. If the schema is incomplete, the process exits with an error and an actionable message (`Run 'orxtra db init' or 'orxtra db migrate apply'`). Always run `orxtra db init` or migrations before starting services after an upgrade.

### Monitoring

- `GET /health` returns `{"status": "ok"}` when the server is ready
- Schema verification failures produce clear error messages at startup
- The dispatch worker logs processing activity and handles SIGINT/SIGTERM
- Tool workers log connection events, reconnection attempts, and tool execution errors
