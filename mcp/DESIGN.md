# MCP Module Design

MCP server exposing the orxt public API as MCP tools. The human's interface via dashboard or conversational AI client.

## Responsibility

Project the services layer as MCP tools. Any MCP client becomes a human interface to a running orxt system.

## MCP Tools

Each tool maps to a service function. Validation tools are omitted (development-time concern, not runtime observation).

### Run Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `start_run` | `run.start_run_from_file` | Start a run from a config file |
| `list_runs` | `run.list_runs` | List all runs |
| `get_run` | `run.get_run` | Get a run's full report |
| `abort_run` | `run.abort_run` | Abort a running run |
| `pause_run` | `run.pause_run` | Pause a running run |
| `resume_run` | `run.resume_run` | Resume a paused run |

### Inbox Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `list_inbox` | `inbox.list_inbox` | List inbox items |
| `get_inbox_item` | `inbox.get_inbox_item` | Get a single inbox item |
| `respond_to_inbox` | `inbox.respond_to_inbox` | Answer an inbox item |
| `skip_inbox_item` | `inbox.skip_inbox_item` | Skip an inbox item |
| `reject_inbox_item` | `inbox.reject_inbox_item` | Reject an inbox item (options insufficient) |

### Trace Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `query_events` | `trace.query_events` | Query events for a run |
| `get_transcript` | `trace.get_transcript` | Get a session transcript |
| `search_transcript` | `trace.search_transcript` | Search a transcript |
| `list_tasks` | `trace.list_tasks` | List tasks for a run |
| `get_task_attempts` | `trace.get_task_attempts` | Get attempts for a task |
| `get_notepad` | `trace.get_notepad` | Get notepad entries |

### Event Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `fire_event` | `events.fire_event` | Fire a named event for wait-for tasks |

### Config Tools

| MCP Tool | Service Function | Description |
|---|---|---|
| `show_config` | `config.dump_config` | Show a run's config snapshot |
| `show_pricing` | `config.show_pricing` | Show the pricing table |

## Live Event Subscription

The MCP server subscribes to PG `LISTEN orxt_events` and pushes event notifications to connected clients.

## Configuration

`db_url` -- PostgreSQL connection URL. Required, no default.

## Transport

JSON-RPC 2.0 over stdio (standard MCP transport).

## Files

| File | Contents |
|---|---|
| `_server.py` | MCP server implementation. JSON-RPC 2.0, tool registry, LISTEN/NOTIFY subscription. |
| `_tools.py` | MCP tool schema definitions derived from service function parameters. |

## What This Module Does NOT Do

- Does not implement business logic (that is services/)
- Does not serve a web dashboard (sibling project)
- Does not add capabilities beyond the services layer
