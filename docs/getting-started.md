---
title: Getting Started
description: Install orxtra, configure an LLM provider, define agents and workflows, run a workflow from the CLI, and inspect results via the trace system.
---

# Getting Started

This guide walks through installing orxtra, configuring a provider, defining an agent, building a workflow, running it, and inspecting the results.

## Prerequisites

- Python 3.12+
- PostgreSQL (for workflow execution and tracing)
- An API key for at least one supported LLM provider (Anthropic, OpenAI, or Google)

## Install

Install the full package:

```bash
pip install orxtra
```

This gives you the `orxtra` CLI and all 26 sub-projects. If you only need a subset, install individual modules:

```bash
pip install orxtra-transport   # typed LLM client only
pip install orxtra-agent       # agent definitions only
pip install orxtra-scheduler   # task execution only
```

Verify the installation:

```bash
orxtra --version
```

## Quick one-shot LLM call

Before setting up workflows, you can use orxtra as a simple LLM client with no database or orchestration:

```python
import asyncio
from orxtra.services import ask

async def main():
    result = await ask(
        prompt="What is structured programming?",
        provider_type="anthropic",
        model="claude-sonnet-4-20250514",
        api_key="sk-ant-...",
    )
    print(result)

asyncio.run(main())
```

A synchronous wrapper is also available:

```python
from orxtra.services import sync_ask

result = sync_ask(
    prompt="What is structured programming?",
    provider_type="anthropic",
    model="claude-sonnet-4-20250514",
    api_key="sk-ant-...",
)
```

Supported provider types: `anthropic`, `openai`, `google`.

## Set up the database

Workflow execution requires PostgreSQL. Create a database and initialize the schema:

```bash
createdb orxtra

orxtra --db "postgresql://localhost/orxtra" db init
```

The `db init` command is idempotent -- running it again skips already-created objects. It also seeds the system principal used for internal attribution.

orxtra requires PostgreSQL 18 or newer: primary keys default to the server's built-in `uuidv7()`, which first ships in 18.

Verify the schema is complete:

```bash
orxtra --db "postgresql://localhost/orxtra" db verify
```

## Configure a provider

Providers are configured in the run config file (covered below in the "Run config" section). Each provider entry needs a `type` field and an `api_key`:

```toml
[provider_configs.anthropic]
type = "anthropic"
api_key = "sk-ant-..."

[provider_configs.openai]
type = "openai"
api_key = "sk-..."
```

The key under `provider_configs` (e.g., `anthropic`, `openai`) is the name used to look up the provider. The `type` field selects the provider implementation. All other fields are passed to the provider constructor.

## Define categories

Categories map agent categories to specific provider/model pairs. Create a `categories.toml` file:

```toml
format_version = 1

[categories]
default = "anthropic/claude-sonnet-4-20250514"
reasoning = "anthropic/claude-opus-4-20250514"
fast = "openai/gpt-4o-mini"
```

The format is `provider_name/model_name`. Each agent declares a `category` which resolves to a provider and model through this mapping.

Validate the file:

```bash
orxtra validate categories categories.toml
```

## Define an agent

An agent is a TOML file paired with a markdown prompt file. Create `agents/writer.toml`:

```toml
format_version = 1

[agent]
name = "writer"
description = "An agent that reads files and writes summaries."
prompt = "writer.md"
category = "default"

[tools]
allow = ["read", "write", "start_task", "end_task"]
```

Key fields:

- `name` -- unique identifier for the agent
- `prompt` -- path to the markdown prompt file, relative to the TOML file
- `category` -- resolves to a provider/model via the categories file
- `tools.allow` -- which tools the agent can use

Create the companion prompt file `agents/writer.md`:

```markdown
You are a writing agent. You read source files and produce clear summaries.

Call start_task before doing any work. Read the files relevant to your
assignment, write your summary, then call end_task with a description of
what you produced.
```

Every agent prompt should instruct the agent to call `start_task` before working and `end_task` when finished. All tool calls require an active task -- calling a tool outside task boundaries is a hard error.

### Available tools

Agents can use these built-in tools (gated by `tools.allow`):

- `read` -- read file contents
- `write` -- write files (with write-safety: atomic replace, stale-write detection)
- `edit` -- edit files
- `git` -- git operations (wraps safegit, concurrency-safe)
- `start_task`, `end_task` -- task lifecycle (required)
- `create_task`, `create_workflow` -- runtime task/workflow creation
- `custom.*` -- wildcard for custom command tools

### Custom tools

Agents can define purpose-built command tools inline:

```toml
[[tools.define]]
name = "pytest"
description = "Run the test suite"
namespace = "custom.exec"
deferred = false

[tools.define.execution]
type = "command"
executable = "pytest"
arg_validation = true
timeout_ceiling = 120
```

Include `custom.*` in the `tools.allow` list to enable custom tools.

Validate the agent:

```bash
orxtra validate agent agents/writer.toml
```

## Define a workflow

A workflow is a DAG of tasks. Create `workflows/summarize.toml`:

```toml
format_version = 1

[workflow]
name = "summarize-project"
description = "Read source files and produce a project summary."

[[tasks]]
name = "analyze"
agent = "writer"
task_prompt = "Read the source files in the project and identify the main components."
timeout = 300

[[tasks]]
name = "summarize"
agent = "writer"
task_prompt = "Write a one-page summary of the project based on the analysis."
depends_on = ["analyze"]
timeout = 300
```

Key fields:

- `workflow.name` -- identifier for the workflow
- `tasks[].name` -- unique name within the workflow
- `tasks[].agent` -- references an agent by name (must match an agent TOML's `agent.name`)
- `tasks[].task_prompt` -- what the agent should do
- `tasks[].depends_on` -- list of task names that must complete first
- `tasks[].timeout` -- maximum seconds for this task

### Post-checks

Tasks can have post-checks that gate exit. If a post-check fails, the agent retries. After retry exhaustion, the task escalates to its parent:

```toml
[[tasks]]
name = "test"
agent = "coder"
task_prompt = "Write tests for the implementation."
depends_on = ["implement"]
timeout = 600

[tasks.postchecks]
scripts = ["myproject.checks:pytest_passes"]
```

Post-check scripts are Python callables referenced as `module:function`.

Validate the workflow:

```bash
orxtra validate workflow workflows/summarize.toml
```

## Create the run config

The run config ties everything together. Create `run.toml`:

```toml
format_version = 1

workflow_path = "workflows/summarize.toml"
agents_dir = "agents"
knowledge_dir = "knowledge"
categories_path = "categories.toml"
read_root = "."
db_url = "postgresql://localhost/orxtra"
budget = "5.00"
autonomy_level = "medium"

[provider_configs.anthropic]
type = "anthropic"
api_key = "sk-ant-..."
```

Required fields:

| Field | Description |
|---|---|
| `workflow_path` | Path to the workflow TOML |
| `agents_dir` | Directory containing agent TOML + md files |
| `knowledge_dir` | Directory for knowledge files loaded into the run |
| `categories_path` | Path to the categories TOML |
| `read_root` | Root directory agents can read from |
| `db_url` | PostgreSQL connection URL |
| `provider_configs` | Map of provider name to config (must include `type` and `api_key`) |
| `budget` | Maximum spend in USD (tracked via internal pricing table) |
| `autonomy_level` | Agent autonomy: `low`, `medium`, or `high` |

Optional fields:

| Field | Description |
|---|---|
| `budget_exhaustion_policy` | What happens when budget runs out: `block_new`, `cancel_all`, `timeout_grace`, or `unlimited` |
| `secrets_env` | Map of secret names to environment variable names |
| `tools_dir` | Directory containing data-defined tool TOML files |

Create the knowledge directory (even if empty, it must exist):

```bash
mkdir -p knowledge
```

## Run the workflow

Start the run via the CLI:

```bash
orxtra --db "postgresql://localhost/orxtra" run start \
    --config run.toml \
    --intent "Summarize the project source code"
```

The command prints the run ID (a UUIDv7) on success. The `--intent` flag is a human-readable description of what this run is for.

### Run lifecycle

You can manage running workflows:

```bash
# List all runs
orxtra --db "postgresql://localhost/orxtra" run list

# Show a run's full report
orxtra --db "postgresql://localhost/orxtra" run show <run_id>

# Pause a running run
orxtra --db "postgresql://localhost/orxtra" run pause <run_id>

# Resume a paused run
orxtra --db "postgresql://localhost/orxtra" run resume <run_id>

# Abort a run
orxtra --db "postgresql://localhost/orxtra" run abort <run_id>
```

## Inspect results via trace

Every tool call, state transition, and event is recorded in the trace store. Query it with the `trace` commands:

### View task statuses

```bash
orxtra --db "postgresql://localhost/orxtra" trace tasks <run_id>
```

Shows each task's status (pending, active, completed, failed, escalated) and attempt count.

### Query events

```bash
# All events for a run (most recent 100)
orxtra --db "postgresql://localhost/orxtra" trace events <run_id>

# Filter by event type
orxtra --db "postgresql://localhost/orxtra" trace events <run_id> --type tool_call

# Increase the limit
orxtra --db "postgresql://localhost/orxtra" trace events <run_id> --limit 500
```

### View a session transcript

```bash
orxtra --db "postgresql://localhost/orxtra" trace transcript <session_id>
```

Shows the full LLM conversation for a task's session, including all messages and tool calls.

### Search transcripts

```bash
orxtra --db "postgresql://localhost/orxtra" trace search <session_id> "error"
```

Case-insensitive substring search across transcript entries.

### View notepad entries

```bash
orxtra --db "postgresql://localhost/orxtra" trace notepad <run_id>
```

Shows cross-agent notepad entries (append-only IPC between agents).

### Output formats

All trace commands support `--format json` for machine-readable output:

```bash
orxtra --db "postgresql://localhost/orxtra" --format json trace tasks <run_id>
```

## Using the Python API

For programmatic use, the services layer provides the same functionality:

```python
import asyncio
import asyncpg
from orxtra.services import start_run, RunConfig
from decimal import Decimal
from pathlib import Path

async def main():
    pool = await asyncpg.create_pool("postgresql://localhost/orxtra")

    config = RunConfig(
        workflow_path=Path("workflows/summarize.toml"),
        agents_dir=Path("agents"),
        knowledge_dir=Path("knowledge"),
        categories_path=Path("categories.toml"),
        read_root=Path("."),
        db_url="postgresql://localhost/orxtra",
        provider_configs={
            "anthropic": {
                "type": "anthropic",
                "api_key": "sk-ant-...",
            },
        },
        budget=Decimal("5.00"),
        autonomy_level="medium",
    )

    run_id = await start_run(
        pool=pool,
        principal_storage=...,  # PgPrincipalStorage(pool)
        caller_principal=...,   # resolved from auth context
        intent="Summarize the project",
        config=config,
    )
    print(f"Run completed: {run_id}")

    await pool.close()

asyncio.run(main())
```

## Project layout

A typical orxtra project looks like this:

```
my-project/
  agents/
    writer.toml
    writer.md
    coder.toml
    coder.md
  workflows/
    summarize.toml
  knowledge/
  categories.toml
  run.toml
```

## Next steps

- Read the [Architecture](architecture.md) doc for the full system design
- See the [CLI Reference](cli-index.md) for all available commands
- Explore the [examples/](https://github.com/smm-h/orxtra/tree/main/examples) directory for more agent and workflow definitions
- Use `orxtra config pricing` to view the internal pricing table for budget planning
