# Notepad Module Design

Filesystem-based IPC for sharing context between agents in a pipeline run.

## Problem

When multiple agents work on the same pipeline (e.g., researcher -> generator -> reviewer), later agents benefit from knowing what earlier agents learned. A generator that knows the researcher found structured data in a specific format can produce better output. Without shared context, each agent starts from scratch.

## Solution: Append-Only Notepad Files

Each pipeline run gets a notepad directory. Agents append entries to shared notepad files. Later agents read the notepad before starting their work. The pipeline executor injects notepad content into agent prompts automatically.

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

Agents write to the notepad via a tool provided by the pipeline executor:

```python
Tool(
    name="notepad",
    description="Record a learning, decision, or issue for other agents in this pipeline.",
    parameters={
        "type": {"type": "string", "enum": ["learning", "decision", "issue"]},
        "text": {"type": "string"}
    },
    execute=...,  # appends to the correct .jsonl file
)
```

The `notepad` tool is a **framework-level tool** -- it exists outside the agent's `allow` whitelist. The pipeline executor injects it into every agent's tool set automatically. It cannot be removed via the whitelist; it is infrastructure, not a domain tool. The `step` and `agent` fields are injected by the executor -- the agent only provides `type` and `text`.

## Read API (Injection)

Before spawning an agent, the pipeline executor:

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
3. This injection is mechanical -- every agent gets the full notepad regardless of whether it asks for it.

## Key Design Decisions

**Append-only.** Agents can only append entries. They cannot edit, delete, or overwrite existing entries. This prevents information loss when multiple agents write concurrently (though in practice, pipeline steps are mostly sequential).

**JSONL, not plain text.** Entries are structured so they can be validated, filtered, and formatted programmatically. A malformed line (not valid JSON, missing required fields) is rejected at write time -- the `notepad` tool returns an error to the agent.

**Three files, not one.** Separating learnings, decisions, and issues makes it possible to inject only relevant context. A generator benefits from learnings but may not need to see issues from an unrelated step. Initially all three are always injected. Selective injection is a future optimization.

**Notepad survives pipeline failure.** If a pipeline aborts mid-execution, the notepad files remain in the run directory. They are artifacts of the run, not temporary state.

**No cross-run notepad.** Each pipeline run gets a fresh notepad. Learnings from previous runs are not carried over automatically. If the user wants persistence across runs, they manage it outside oxtra (e.g., a project-level knowledge file that agents are instructed to read).

## What This Module Does NOT Do

- Does not provide cross-run persistence
- Does not implement search or retrieval over notepad entries
- Does not limit notepad size (the consuming project should monitor this)
- Does not encrypt or protect notepad contents
