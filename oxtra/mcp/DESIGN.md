# MCP Module Design

MCP server exposing the oxtra public API as MCP tools. The human's interface via dashboard or conversational AI client.

## Responsibility

Project the services layer as MCP tools. Any MCP client -- including a conversational AI agent in a dashboard, Claude Desktop, or other MCP-aware tools -- becomes a human interface to a running oxtra system.

## MCP Tools

Each tool maps 1:1 to a service function. The MCP server adds no logic -- it translates between the MCP protocol and the services layer.

### Run Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `list_runs` | `run.list_runs` | List all runs |
| `get_run` | `run.get_run` | Get a run's full report |
| `abort_run` | `run.abort_run` | Abort a running run |

### Inbox Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `list_inbox` | `inbox.list_inbox` | List inbox items |
| `get_inbox_item` | `inbox.get_inbox_item` | Get a single inbox item |
| `respond_to_inbox` | `inbox.respond_to_inbox` | Answer an inbox item |
| `skip_inbox_item` | `inbox.skip_inbox_item` | Skip an inbox item |

### Trace Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `query_events` | `trace.query_events` | Query events for a run |
| `get_transcript` | `trace.get_transcript` | Get a session transcript |
| `get_step_attempts` | `trace.get_step_attempts` | Get attempts for a step |
| `get_notepad` | `trace.get_notepad` | Get notepad entries |

### Config Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `show_config` | `config.dump_config` | Show a run's config snapshot |
| `show_pricing` | `config.show_pricing` | Show the pricing table |

## Live Event Subscription

The MCP server subscribes to PG `LISTEN oxtra_events` and can push event notifications to connected clients via MCP's notification mechanism (if the client supports it). This enables real-time dashboards without polling.

## Configuration

The MCP server requires:

- `db_url` -- PostgreSQL connection URL. Required, no default.

The server connects to PG at startup, creates a connection pool, and passes it to all service function calls.

## Transport

The MCP server communicates via JSON-RPC 2.0 over stdio (standard MCP transport). It can be configured as an MCP server in any MCP-aware client.

## Files

| File | Contents |
|---|---|
| `_server.py` | MCP server implementation. JSON-RPC 2.0 handler, tool registry mapping MCP tool names to service functions, LISTEN/NOTIFY subscription for live events. |
| `_tools.py` | MCP tool schema definitions. Each tool's name, description, and input_schema (JSON Schema) derived from the corresponding service function's parameters. |

## What This Module Does NOT Do

- Does not implement business logic (that's services/)
- Does not own the database schema (that's trace/)
- Does not serve a web dashboard (that's a sibling project)
- Does not implement agent execution or scheduling
- Does not add capabilities beyond what the services layer provides
