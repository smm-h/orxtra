# CLI Module Design

strictcli-based command-line interface. Thin frontend over the services layer.

## Responsibility

Parse arguments, call service functions, format output. No business logic. Agents are the primary users -- the CLI is designed for scriptability, not interactive human workflows.

## Framework

Built on **strictcli**. Schema-driven, no implicit flags, strict argument validation. The CLI schema is auto-dumped during releases via rlsbl.

## Commands

### Run Commands

| Command | Description |
|---|---|
| `orxt run list` | List all runs, newest first. |
| `orxt run show <run_id>` | Show a run's full report. |
| `orxt run abort <run_id>` | Signal a running run to abort. |

### Inbox Commands

| Command | Description |
|---|---|
| `orxt inbox list --run <run_id> [--status pending]` | List inbox items. |
| `orxt inbox show <item_id>` | Show a single inbox item with full context. |
| `orxt inbox respond <item_id> <answer>` | Answer an inbox item. |
| `orxt inbox skip <item_id>` | Skip an inbox item (bless the assumption). |

### Trace Commands

| Command | Description |
|---|---|
| `orxt trace events <run_id> [--type <event_type>] [--limit N]` | Query events. |
| `orxt trace transcript <session_id>` | Show a session's full transcript. |
| `orxt trace steps <run_id>` | Show step statuses and attempt counts. |
| `orxt trace notepad <run_id>` | Show notepad entries. |

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

## Output Formatting

- **table**: human-readable tables for terminal display (default)
- **json**: machine-parseable JSON for agent consumption

When `--format json` is used, all output is valid JSON to stdout. Errors go to stderr.

## Entry Point

The CLI is registered as a console script entry point in `pyproject.toml`:

```toml
[project.scripts]
orxt = "cli._cli:main"
```

## Files

| File | Contents |
|---|---|
| `_cli.py` | strictcli application definition. Command groups, argument schemas, dispatch to service functions. Entry point `main()`. |
| `_formatters.py` | Output formatting: table and JSON renderers for each service result type. |

## What This Module Does NOT Do

- Does not implement business logic (that's services/)
- Does not manage database connections beyond passing `--db` to the pool
- Does not provide interactive modes (agents script individual commands)
- Does not start runs (that's the Python API; the CLI is for inspection and inbox interaction)
