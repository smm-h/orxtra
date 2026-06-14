# Notepad Module Design

Cross-agent context sharing within a run, backed by PostgreSQL.

## Problem

When multiple agents work on the same run (e.g., researcher -> generator -> reviewer), later agents benefit from knowing what earlier agents learned. Without shared context, each agent starts from scratch.

## Solution: Append-Only Notepad Entries

Agents append entries to the run's notepad via the `notepad` tool. Later agents read the notepad before starting their work. The scheduler injects notepad content into agent prompts automatically.

## Storage

Notepad entries are stored in the `notepad_entries` table (owned by the trace module). Append-only -- `REVOKE UPDATE, DELETE` at the DB-role level.

## Entry Schema

Each entry:

| Field | Type | Required | Description |
|---|---|---|---|
| `step_name` | string | yes | Workflow step that wrote this entry |
| `agent_name` | string | yes | Agent name |
| `entry_type` | string | yes | `learning`, `decision`, `issue` |
| `text` | string | yes | Free-form content. One fact/decision/issue per entry. |

The `step_name` and `agent_name` fields are injected by the executor -- the agent only provides `entry_type` and `text`.

## Write API

Agents write to the notepad via the `notepad` tool, constructed by `make_notepad_tool(trace_writer)` (see `tool/DESIGN.md`). The tool is a regular tool -- agents include `"notepad"` in their `allow` list to get access.

## Read API (Injection)

Before spawning an agent, the scheduler:

1. Reads all notepad entries for the current run from PG
2. Formats them as a section appended to the agent's prompt:
   ```
   ## Context from previous steps

   ### Learnings
   - [research/researcher] Source data is available as structured JSON via the public API.

   ### Decisions
   - [generate/generator] Using API-based extraction instead of HTML parsing.

   ### Issues
   - (none)
   ```
3. This injection is mechanical -- every agent in the run gets the full notepad content, regardless of whether it has `"notepad"` in its `allow` list. Reading and writing are separate concerns: all agents benefit from prior learnings, but only agents with `"notepad"` in their `allow` list can write new entries.

## Key Design Decisions

**Append-only.** Agents can only append entries. They cannot edit, delete, or overwrite existing entries.

**Three types, not one.** Separating learnings, decisions, and issues makes it possible to format and potentially filter by category. Initially all three are always injected.

**Notepad survives run failure.** Entries remain in PG. They are artifacts of the run, not temporary state.

**No cross-run notepad.** Each run starts with an empty notepad. Cross-run persistence is the knowledge base (lessons table in the Overseer's memory).

## Files

| File | Contents |
|---|---|
| `_types.py` | `NotepadEntry` pydantic model: step_name, agent_name, text, entry_type. |
| `_reader.py` | `read_notepad(pool, run_id)` -- reads entries from PG, returns structured entries. `format_notepad(entries)` -- formats entries as the markdown section injected into agent prompts. |

## What This Module Does NOT Do

- Does not provide cross-run persistence (that's the Overseer's lessons table)
- Does not implement search or retrieval over entries
- Does not limit notepad size
- Does not encrypt or protect notepad contents
- Does not write to PG directly (delegates to the trace module's `write_notepad_entry`)
