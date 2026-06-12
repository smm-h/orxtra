# oxtra -- Design

## What oxtra Is

A Python library for autonomous multi-agent AI workflows. You provide intent; oxtra drives it to completion. An Overseer (persistent LLM with read-only tools and structured memory) makes judgment calls and generates workflows. A Scheduler (deterministic event loop) validates, executes, and enforces. Agent steps (scoped LLM calls) do the actual work.

## Project Structure

```
oxtra/
    DESIGN.md
    CLAUDE.md
    README.md
    pyproject.toml
    selfdoc.json
    .rlsbl/                    # rlsbl release scaffolding
    docs/                      # selfdoc templates
    todo/                      # Work items
    scripts/                   # Reusable project scripts
    oxtra/
        __init__.py            # Public API: re-exports from submodules
        overseer/              # The brain: persistent LLM, decisions, memory, learning
        scheduler/             # The nervous system: event loop, validation, execution
        agent/                 # Agent definition loading and validation
        tool/                  # Tool contract, registry, constructors
        transport/             # LLM communication via Provider protocol
            providers/         # Per-provider API implementations
        verify/                # Mechanical + semantic verification
        notepad/               # Cross-agent context sharing
        session/               # Session lifecycle, cost tracking
        trace/                 # Run directory persistence and query
    tests/                     # One test module per source module
```

Each module directory contains a `DESIGN.md` (the spec) and Python files (the implementation). See each module's DESIGN.md for its file listing.

## What oxtra Is NOT

- **Not a plugin for OpenCode or any other agent runtime.** oxtra is standalone. It does not extend, wrap, or integrate into an existing agent system.
- **Not a TUI or CLI application.** oxtra is a library. It exposes Python APIs. If you want a CLI, build one on top.
- **Not a prompt engineering framework.** It loads prompts from files and passes them through. It does not template, optimize, or manipulate prompt content.
- **Not a model router with fallback chains.** Category-to-model is a flat dictionary lookup. If the category is missing, it errors. No fallback, no retry with a different model.

## Architecture

Three components, nine modules.

The **Overseer** is the brain. The **Scheduler** is the nervous system. **Agent steps** are the hands.

| Module | Responsibility |
|---|---|
| `overseer/` | Persistent LLM with read-only tools and SQLite memory. Makes judgment calls via structured decision protocols. Generates workflows. Manages assumptions, constraints, lessons. Session handoff when context fills. |
| `scheduler/` | Deterministic event loop. Validates and executes workflows. Enforces budgets and mechanical constraints. Routes events to the Overseer. Classifies errors. Manages pause/resume and crash recovery. Has no opinions. |
| `agent/` | Load agent definitions from TOML + .md prompt files. Validate schema. Resolve categories and permissions. |
| `tool/` | Tool registry. Each tool is a single Python object: name, description, parameters, execute. No separation between schema and implementation. |
| `transport/` | LLM client via Provider protocol. Send messages to LLM APIs directly (Anthropic, OpenAI), stream responses, parse events, run the tool-call loop. No subprocess agents. |
| `verify/` | Run verification after each step. Two tiers: Python callables (mechanical gate), then verification agents (semantic checks). |
| `notepad/` | Append-only filesystem-based IPC for cross-agent context sharing. Each run gets a directory. Workers append learnings, decisions, issues. No overwrites. |
| `session/` | Session lifecycle management wrapping transport. Track session IDs for resumption. Track cost (tokens, USD). |
| `trace/` | Persistence layer for runs. Owns the run directory structure: step results, transport event logs, session transcripts, Overseer checkpoints. Enables crash recovery and session handoff. |

## Design Axioms

Twelve hard rules. Each is mechanically enforced, not prompt-requested.

1. **Agents are data, not code.** TOML metadata + .md prompts. No factory functions, no classes, no lifecycle methods. An agent definition is a static document that the framework loads and interprets.

2. **Tools are single typed objects.** `{name, description, parameters, execute}` -- schema and implementation are one object. No decoupled registries where schema lives in TOML and implementation lives elsewhere.

3. **Categories abstract model names.** Agents and pipeline steps reference intent strings ("quick", "deep", "visual"), never model names. A flat map resolves intent to model. No fallback chains. Missing category is a hard error.

4. **Permissions are whitelists.** Each agent declares which tools it can use. Everything else is mechanically absent -- the LLM never sees unlisted tools. Not a prompt instruction. No deny lists, no RBAC, no inheritance. This includes framework-provided tools like `spawn`, `consult`, and `notepad` -- they follow the same whitelist rules as any domain tool. There is no separate 'framework tool' category.

5. **Subagents cannot delegate -- enforced mechanically.** Worker agents spawned by a pipeline step do not have access to the `spawn` tool. This prevents orchestration recursion. The constraint is in the tool list construction, not in the prompt.

6. **Two-tier delegation.** Two levels of agent invocation: `spawn` (full agent with write access, orchestrator-only) and `consult` (read-only agent for research, available to workers). Workers can research but cannot spawn other workers.

7. **Mandatory parameters for consequential choices.** No implicit defaults for provider selection, model choice, or execution mode. Missing values are hard errors, not silent defaults.

8. **Verification is mechanical, not requested.** After every step, verification runs automatically. The scheduler runs verification functions -- not the agent's prompt. The agent cannot skip verification. Verification agents are full agent definitions (TOML + .md prompt file), not framework-constructed templates. The scheduler invokes them via `consult`, injecting a verification context struct as template variables.

9. **Filesystem IPC + session resumption.** Cross-agent context via append-only notepad files. Session continuity via session IDs returned from every invocation. No in-memory shared state between agents.

10. **Auto-continuation.** The scheduler refuses to stop while steps remain incomplete. If a step fails and retries are available, the Overseer decides strategy. If a step succeeds, the scheduler moves to the next. Only exhausted retries, explicit abort, or budget exhaustion stop execution.

11. **The Overseer is the only long-lived entity.** Agent steps are scoped and short-lived -- they get a task, do it, and report back within their context window. If an agent step can't finish within its context window, that's a decomposition problem. Only the Overseer receives session handoff when its context fills: summary + UUID for querying the full transcript via trace/.

12. **Structured decisions, not free-form.** The Overseer makes decisions via typed protocols with closed output schemas. It picks from menus, never free-forms. If a situation doesn't match any registered protocol, it escalates to the human.

## Anti-Patterns

Ten patterns to avoid, identified from analysis of oh-my-openagent (omo) -- a similar project that got many things right but suffered from complexity creep.

1. **No config sprawl.** The configuration surface is: agent TOML files, pipeline TOML files, one categories TOML file, and Python tool definitions. No 100-knob config objects with nested sections.

2. **No hook/middleware system.** Behavior is in the pipeline executor, not in interceptor chains. No lifecycle hooks, no plugin registry, no event bus.

3. **No built-in agents.** oxtra is a framework. It defines zero agents -- those are the user's domain. The framework provides loading, validation, and execution. It does not ship "Sisyphus" or "Atlas."

4. **No model routing complexity.** Category to model is a flat dictionary lookup. No fallback chains, no fuzzy matching, no availability checks, no provider resolution layers.

5. **No massive functions.** No module should have a function over ~100 lines. If it's getting big, decompose.

6. **No bloated background manager.** Async agent execution uses standard asyncio. One completion detection path, not three.

7. **No skill system.** Agents get their prompt from a .md file. That's it. No skill loaders, mergers, MCP managers, frontmatter parsers.

8. **No prompts in code.** All prompt text lives in .md files, never in Python strings. Python files contain logic, not prose.

9. **No feature flags.** Features are either shipped or not. No `experimental` config sections, no `enabled: false` defaults.

10. **No unvalidated filesystem IPC.** Notepad entries have a defined schema. Malformed writes are rejected.

## Examples

### Agent Definition

```toml
# agents/researcher.toml
[agent]
name = "researcher"
description = "Gathers information from web pages, extracting structure and content"
prompt = "researcher.md"
category = "quick"

[tools]
allow = ["navigate", "screenshot", "extract_content", "read", "bash", "consult", "notepad"]
```

No `[permissions]` section -- permissions are expressed entirely through the `allow` whitelist. `spawn` is mechanically stripped from all spawned agents regardless of config, so it never needs to be mentioned. `consult` and `notepad` are listed explicitly because the agent should be able to research via read-only agents and record learnings for downstream pipeline steps.

The prompt file (`researcher.md`) lives alongside the TOML file and contains the agent's system prompt. It can reference `{variable}` placeholders that are substituted at spawn time from pipeline step variables.

### Pipeline Definition

```toml
# pipelines/process.toml
[pipeline]
name = "process-data"
description = "Full processing pipeline: research, generate, review"

[[steps]]
name = "research"
agent = "researcher"
task = "Investigate {target}: gather relevant pages, extract key content and structure."
variables = ["target", "work_dir"]
depends_on_previous = false
timeout = 300
verify = "myproject.verify:research_complete"

[[steps]]
name = "generate"
agent = "generator"
category = "deep"
task = "Generate output for {target} based on the research data in {work_dir}."
variables = ["target", "work_dir", "output_path"]
depends_on = ["research"]
timeout = 600
verify = "myproject.verify:output_valid"

[[steps]]
name = "review"
agent = "reviewer"
task = "Run the test harness against the output at {output_path}."
variables = ["target", "output_path"]
depends_on = ["generate"]
timeout = 300
retry = 5
retry_resume = true
retry_inject_failure = true
verify = "myproject.verify:review_passed"
```

Key points:
- Every step must declare dependencies explicitly via `depends_on` (list of step names) or `depends_on_previous` (boolean). Both missing is a hard error.
- `timeout` is required on every agent step (seconds). No default.
- `verify` references a Python callable (`module:function`) that runs after the step completes.
- `category` on a step overrides the agent's default category for that invocation.
- `retry` sets the maximum number of retries before the executor gives up on a step.
- `retry_resume` is required when `retry > 0`. True continues the existing session on retry; false starts a fresh session.
- `variables` declares which pipeline variables the step needs. Missing variables at runtime are hard errors.

### Tool-Less Agent

```toml
# agents/extractor.toml
[agent]
name = "extractor"
description = "Extracts structured information from unstructured text documents"
prompt = "extractor.md"
category = "standard"

[tools]
allow = []
```

Agents with an empty tool list are a first-class pattern. The agent receives only the task prompt and produces text output. No tools are offered to the LLM. This is appropriate for classification, extraction, summarization, and other pure text-to-text tasks.

### Categories

```toml
# categories.toml
[categories]
quick = "anthropic/claude-haiku-4-5"
standard = "anthropic/claude-sonnet-4-6"
deep = "anthropic/claude-opus-4-6"
visual = "google/gemini-2.5-flash"
```

One file, one flat map. No nesting, no provider sections, no fallback lists.

## Example Consumer

A consuming project defines all domain-specific content. oxtra provides the framework; the consumer provides:

- **Agents** (researcher, generator, reviewer, extractor, etc.) as TOML + .md files
- **Tools** (navigate, screenshot, extract_content, etc.) as Python tool objects
- **Pipelines** (process-data, etl-pipeline, etc.) as TOML step files
- **Verification functions** (research_complete, output_valid, review_passed) as Python callables

The consumer builds a tool registry from oxtra's tool constructors (`make_read_tool`, `make_write_tool`, `make_bash_tool`, `make_spawn_tool`, `make_consult_tool`, `make_notepad_tool`, etc.) plus any custom domain tools, then calls oxtra's Python API to run pipelines. oxtra handles the rest: agent loading, tool filtering, model selection, step execution, verification, retries, notepad IPC, and session tracking.

### Mixed Pipeline Example

A pipeline that mixes function steps (fetch, normalize, unify) with agent steps (extract) demonstrates several oxtra features: function steps for deterministic work, structured output validation against a JSON Schema, `for_each` for batch processing, and retry with failure injection.

```toml
[pipeline]
name = "etl-pipeline"
description = "Fetch, normalize, extract, and publish structured data"

[[steps]]
name = "fetch"
callable = "myproject.steps:fetch_data"
variables = ["data_dir"]
depends_on_previous = false

[[steps]]
name = "normalize"
callable = "myproject.steps:normalize_all"
variables = ["data_dir"]
depends_on = ["fetch"]

[[steps]]
name = "unify"
callable = "myproject.steps:unify_records"
variables = ["data_dir"]
depends_on = ["normalize"]

[[steps]]
name = "extract"
agent = "extractor"
task = "Extract key fields and a summary from this document.\n\nTitle: {item.title}\n\nContent:\n{item.body}"
for_each = "documents_to_process"
for_each_abort_on_failure = false
variables = ["documents_to_process"]
category = "standard"
output_schema = "schemas/extraction.json"
depends_on = ["unify"]
timeout = 120
retry = 2
retry_resume = false
retry_inject_failure = true
verify = "myproject.verify:extraction_valid"

[[steps]]
name = "publish"
callable = "myproject.steps:write_output"
variables = ["data_dir"]
depends_on = ["extract"]
```
