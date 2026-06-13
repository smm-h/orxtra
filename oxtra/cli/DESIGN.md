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
| `oxtra run list` | List all runs, newest first. |
| `oxtra run show <run_id>` | Show a run's full report. |
| `oxtra run abort <run_id>` | Signal a running run to abort. |

### Inbox Commands

| Command | Description |
|---|---|
| `oxtra inbox list --run <run_id> [--status pending]` | List inbox items. |
| `oxtra inbox show <item_id>` | Show a single inbox item with full context. |
| `oxtra inbox respond <item_id> <answer>` | Answer an inbox item. |
| `oxtra inbox skip <item_id>` | Skip an inbox item (bless the assumption). |

### Trace Commands

| Command | Description |
|---|---|
| `oxtra trace events <run_id> [--type <event_type>] [--limit N]` | Query events. |
| `oxtra trace transcript <session_id>` | Show a session's full transcript. |
| `oxtra trace steps <run_id>` | Show step statuses and attempt counts. |
| `oxtra trace notepad <run_id>` | Show notepad entries. |

### Validation Commands

| Command | Description |
|---|---|
| `oxtra validate agent <path>` | Validate an agent TOML file. |
| `oxtra validate workflow <path>` | Validate a workflow TOML file. |
| `oxtra validate categories <path>` | Validate a categories TOML file. |

### Config Commands

| Command | Description |
|---|---|
| `oxtra config show <run_id>` | Show the config snapshot for a run. |
| `oxtra config pricing` | Show the current internal pricing table. |

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
oxtra = "oxtra.cli:main"
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
