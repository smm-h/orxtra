# oxtra

A Python library for orchestrating multi-agent AI workflows.

Define agents as TOML + markdown. Define tools as Python objects. Define pipelines as TOML step files. oxtra handles execution, delegation, verification, session management, and cross-agent context sharing.

## Overview

oxtra is a framework, not an application. It defines zero agents, zero tools, and zero pipelines. The consuming project provides all domain-specific content; oxtra provides the runtime.

### Agent definitions

```toml
# agents/researcher.toml
[agent]
name = "researcher"
description = "Gathers information from web pages"
prompt = "researcher.md"
category = "quick"

[tools]
allow = ["read", "bash", "consult", "notepad"]
```

Agents are static data documents (TOML metadata + markdown prompt), not code. The `allow` whitelist controls which tools the agent can use. Tools not listed are mechanically absent from the LLM's perspective.

### Pipelines

```toml
# pipelines/process.toml
[pipeline]
name = "process-data"
description = "Research, generate, review"

[[steps]]
name = "research"
agent = "researcher"
task = "Investigate {target}."
variables = ["target"]
depends_on_previous = false
timeout = 300
verify = "myproject.verify:research_complete"

[[steps]]
name = "generate"
agent = "generator"
category = "deep"
task = "Generate output for {target}."
variables = ["target", "output_path"]
depends_on = ["research"]
timeout = 600
verify = "myproject.verify:output_valid"
```

Pipelines declare steps with dependencies, timeouts, verification, and retry policy. The executor builds a dependency graph and runs independent steps in parallel. Steps can be agent-driven (LLM) or function-driven (Python callable).

### Tools

```python
from oxtra.tool import Tool

async def search(args: dict) -> str:
    query = args["query"]
    return f"Results for: {query}"

search_tool = Tool(
    name="search",
    description="Search for information",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    execute=search,
)
```

Tools are single objects that bundle schema and implementation. oxtra provides constructors for common tools (`make_read_tool`, `make_bash_tool`, `make_spawn_tool`, etc.) but none are mandatory.

### Categories

```toml
# categories.toml
[categories]
quick = "anthropic/claude-haiku-4-5"
standard = "anthropic/claude-sonnet-4-6"
deep = "anthropic/claude-opus-4-6"
```

A flat map from intent to model. No fallback chains, no fuzzy matching. Missing category is a hard error.

## Design principles

- **Agents are data, not code.** TOML + markdown. No factory functions, no classes, no lifecycle methods.
- **Permissions are whitelists.** The LLM never sees unlisted tools. Mechanically enforced, not prompt-instructed.
- **Subagents cannot delegate.** `spawn` is mechanically stripped from spawned agents. Workers can `consult` read-only agents for research.
- **Verification is mechanical.** The executor runs verification after every step. Two tiers: Python callables (fast, deterministic) then verification agents (slow, semantic).
- **No implicit defaults.** Provider, model, timeout, retry behavior -- all must be explicit.
- **No config sprawl.** Agent TOMLs, pipeline TOMLs, one categories TOML, Python tools. No 100-knob config objects.

## Architecture

Eight modules, each with a single responsibility:

| Module | Purpose |
|---|---|
| `agent/` | Load and validate agent definitions |
| `tool/` | Tool contract, registry, constructors |
| `transport/` | LLM communication via Provider protocol |
| `pipeline/` | Pipeline execution, dependency graph, parallelism |
| `verify/` | Mechanical + semantic verification |
| `notepad/` | Cross-agent context sharing |
| `session/` | Session lifecycle, cost tracking, context handoff |
| `trace/` | Run directory persistence and query |

See `DESIGN.md` for the full architecture, design axioms, and anti-patterns.

## Status

Design phase. Implementation has not started.
