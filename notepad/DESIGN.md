# Notepad Module Design

Cross-agent context sharing within a run, backed by PostgreSQL.

## Problem

When multiple agents work on the same run (e.g., researcher -> generator -> reviewer), later agents benefit from knowing what earlier agents learned. Without shared context, each agent starts from scratch.

## Solution: Append-Only Notepad Entries

Agents append entries to the run's notepad via the `notepad` tool. Later agents read the notepad before starting their work. The scheduler injects notepad content into agent prompts automatically.

## Storage

Notepad entries are stored in the `notepad_entries` table (owned by the trace module). Append-only -- `REVOKE UPDATE, DELETE`.

## Entry Schema

| Field | Type | Required | Description |
|---|---|---|---|
| `task_name` | string | yes | Task that wrote this entry |
| `agent_name` | string | yes | Agent name |
| `entry_type` | string | yes | `learning`, `decision`, `issue` |
| `text` | string | yes | One fact/decision/issue per entry |

The `task_name` and `agent_name` fields are injected by the executor.

## Write API

Via the `notepad` tool, constructed by `make_notepad_tool(trace_writer)`. Agents include `"notepad"` in their `allow` list.

## Read API (Injection)

Before spawning an agent, the scheduler reads all notepad entries for the current run and appends them to the agent's prompt. Injection is mechanical -- every agent gets the full notepad, regardless of whether it has `"notepad"` in its `allow` list.

## Key Design Decisions

- **Append-only.** No edits, deletes, or overwrites.
- **Three types.** Learnings, decisions, issues -- separate categories for formatting and filtering.
- **Notepad survives run failure.** Entries are artifacts.
- **No cross-run notepad.** Each run starts empty. Cross-run persistence is the lessons table.

## Files

| File | Contents |
|---|---|
| `_types.py` | `NotepadEntry` pydantic model. |
| `_reader.py` | `read_notepad(pool, run_id)`, `format_notepad(entries)`. |

## What This Module Does NOT Do

- Does not provide cross-run persistence (that is the lessons table)
- Does not search or filter entries
- Does not write to PG directly (delegates to trace)
