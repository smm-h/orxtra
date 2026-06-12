# Notepad Module Design

Filesystem-based IPC for sharing context between agents in a pipeline run.

## Problem

When multiple agents work on the same pipeline (e.g., researcher -> generator -> reviewer), later agents benefit from knowing what earlier agents learned. A generator that knows the researcher found structured data in a specific format can produce better output. Without shared context, each agent starts from scratch.

## Solution: Append-Only Notepad Files

Each pipeline run gets a notepad directory. Agents append entries to shared notepad files. Later agents read the notepad before starting their work. The scheduler injects notepad content into agent prompts automatically.

## Directory Structure

```
{run_dir}/notepad/
    learnings.jsonl    # Patterns discovered, conventions, technical facts
    decisions.jsonl    # Architectural choices, design decisions
    issues.jsonl       # Problems encountered, blockers, warnings
```

## Entry Schema

Each line in a `.jsonl` file is a JSON object:

```json
{"step": "research", "agent": "researcher", "text": "Source data is available as structured JSON via the public API, no scraping needed."}
{"step": "generate", "agent": "generator", "text": "Using API-based extraction instead of HTML parsing for primary data."}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `step` | string | yes | Pipeline step name that wrote this entry |
| `agent` | string | yes | Agent name |
| `text` | string | yes | Free-form content. One fact/decision/issue per entry. |

## Write API

Agents write to the notepad via the `notepad` tool, constructed by `make_notepad_tool(run_dir)` (see `tool/DESIGN.md` for the tool spec). The tool is a regular tool -- agents include `"notepad"` in their `allow` list to get access. Agents without it in their `allow` list cannot write to the notepad.

The `step` and `agent` fields in each JSONL entry are injected by the executor -- the agent only provides `type` and `text`.

## Read API (Injection)

Before spawning an agent, the scheduler:

1. Reads all notepad files
2. Formats them as a section appended to the agent's prompt:
   ```
   ## Context from previous steps

   ### Learnings
   - [research/researcher] Source data is available as structured JSON via the public API, no scraping needed.

   ### Decisions
   - [generate/generator] Using API-based extraction instead of HTML parsing for primary data.

   ### Issues
   - (none)
   ```
3. This injection is mechanical -- every agent in the pipeline gets the full notepad content injected, regardless of whether the agent has `"notepad"` in its `allow` list. Reading notepad context and writing notepad entries are separate concerns: all agents benefit from prior learnings, but only agents with `"notepad"` in their `allow` list can write new entries.

## Key Design Decisions

**Append-only.** Agents can only append entries. They cannot edit, delete, or overwrite existing entries. This prevents information loss when multiple agents write concurrently (though in practice, pipeline steps are mostly sequential).

**JSONL, not plain text.** Entries are structured so they can be validated, filtered, and formatted programmatically. A malformed line (not valid JSON, missing required fields) is rejected at write time -- the `notepad` tool returns an error to the agent.

**Three files, not one.** Separating learnings, decisions, and issues makes it possible to inject only relevant context. A generator benefits from learnings but may not need to see issues from an unrelated step. Initially all three are always injected. Selective injection is a future optimization.

**Notepad survives pipeline failure.** If a pipeline aborts mid-execution, the notepad files remain in the run directory. They are artifacts of the run, not temporary state.

**No cross-run notepad.** Each pipeline run gets a fresh notepad. Learnings from previous runs are not carried over automatically. If the user wants persistence across runs, they manage it outside oxtra (e.g., a project-level knowledge file that agents are instructed to read).

## Files

| File | Contents |
|---|---|
| `_types.py` | `NotepadEntry` frozen dataclass: step, agent, text, type (learning/decision/issue). |
| `_reader.py` | `read_notepad(run_dir)` -- reads all three JSONL files, returns structured entries. `format_notepad(entries)` -- formats entries as the markdown section injected into agent prompts. |

## What This Module Does NOT Do

- Does not provide cross-run persistence
- Does not implement search or retrieval over notepad entries
- Does not limit notepad size (the consuming project should monitor this)
- Does not encrypt or protect notepad contents
