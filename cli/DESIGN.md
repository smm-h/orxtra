# CLI Module Design

strictcli-based command-line interface. Thin frontend over the services layer.

## Responsibility

Parse arguments, call service functions, format output. No business logic. Agents are the primary users.

## Framework

Built on **strictcli**. Schema-driven, no implicit flags, strict argument validation.

## Commands

### Run Commands

| Command | Description |
|---|---|
| `orxt run start --config <path> --intent "..."` | Start a run from a config file. |
| `orxt run list` | List all runs, newest first. |
| `orxt run show <run_id>` | Show a run's full report. |
| `orxt run abort <run_id>` | Signal a running run to abort. |
| `orxt run pause <run_id>` | Pause a running run. |
| `orxt run resume <run_id>` | Resume a paused run. |

### Inbox Commands

| Command | Description |
|---|---|
| `orxt inbox list --run <run_id> [--status pending]` | List inbox items. |
| `orxt inbox show <item_id>` | Show a single inbox item. |
| `orxt inbox respond <item_id> <answer>` | Answer an inbox item. |
| `orxt inbox skip <item_id>` | Skip an inbox item. |
| `orxt inbox reject <item_id> <reason>` | Reject an inbox item (options insufficient). |

### Trace Commands

| Command | Description |
|---|---|
| `orxt trace events <run_id> [--type <event_type>] [--limit N]` | Query events. |
| `orxt trace transcript <session_id>` | Show a session's full transcript. |
| `orxt trace search <session_id> <query>` | Search a transcript (case-insensitive substring). |
| `orxt trace tasks <run_id>` | Show task statuses and attempt counts. |
| `orxt trace notepad <run_id>` | Show notepad entries. |

### Event Commands

| Command | Description |
|---|---|
| `orxt event fire <run_id> <event_name> [--payload '...']` | Fire a named event for wait-for tasks. |

### Validation Commands

| Command | Description |
|---|---|
| `orxt validate agent <path>` | Validate an agent TOML file. |
| `orxt validate workflow <path>` | Validate a workflow TOML file. |
| `orxt validate categories <path>` | Validate a categories TOML file. |

### Config Commands

| Command | Description |
|---|---|
| `orxt config show <run_id>` | Show the config snapshot for a run. |
| `orxt config pricing` | Show the current internal pricing table. |

## Global Flags

| Flag | Description |
|---|---|
| `--db` | PostgreSQL connection URL. Required (no default). |
| `--format` | Output format: `table` (default), `json`. |
| `--quiet` | Suppress non-essential output. |

## Entry Point

```toml
[project.scripts]
orxt = "orxt.cli._cli:main"
```

## Files

| File | Contents |
|---|---|
| `_cli.py` | strictcli application definition. Command groups, dispatch to service functions. Entry point `main()`. |
| `_formatters.py` | Output formatting: table and JSON renderers. |

## What This Module Does NOT Do

- Does not implement business logic (that is services/)
- Does not manage database connections beyond passing `--db`
- Does not provide interactive modes
