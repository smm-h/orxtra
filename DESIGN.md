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
    knowledge/                 # Consumer domain knowledge (.md and .toml)
    oxtra/
        __init__.py            # Public API: re-exports from submodules
        overseer/              # The brain: persistent LLM, decisions, memory, learning
        scheduler/             # The nervous system: event loop, validation, execution
        agent/                 # Agent definition loading and validation
        tool/                  # Tool contract, registry, constructors (no bash tool)
        transport/             # LLM communication via Provider protocol
            providers/         # Per-provider raw httpx implementations
        verify/                # Mechanical chains + semantic verification agents
        notepad/               # Cross-agent context sharing (PG-backed)
        session/               # Session lifecycle, token tracking
        trace/                 # PG schema owner: events, results, transcripts, inbox
        services/              # Shared business logic consumed by all frontends
        cli/                   # strictcli CLI (agents are the primary users)
        mcp/                   # MCP server exposing the public API
    tests/                     # One test module per source module
```

Each module directory contains a `DESIGN.md` (the spec) and Python files (the implementation). See each module's DESIGN.md for its file listing.

## What oxtra Is NOT

- **Not a plugin for OpenCode or any other agent runtime.** oxtra is standalone. It does not extend, wrap, or integrate into an existing agent system.
- **Not a prompt engineering framework.** It loads prompts from files and passes them through. It does not template, optimize, or manipulate prompt content.
- **Not a model router with fallback chains.** Category-to-model is a flat dictionary lookup. If the category is missing, it errors. No fallback, no retry with a different model.
- **Not a plugin system.** No language plugins, toolchain registries, write hooks, or aspect generators. Consumers extend oxtra via Tool objects, verify callables, and constructor parameters.

## Architecture

Three components, twelve modules.

The **Overseer** is the brain. The **Scheduler** is the nervous system. **Agent steps** are the hands.

| Module | Responsibility |
|---|---|
| `overseer/` | Persistent LLM with read-only tools and PostgreSQL memory. Makes judgment calls via structured decision protocols. Generates workflows. Manages assumptions, constraints, lessons. Session handoff when context fills. |
| `scheduler/` | Deterministic event loop. Validates and executes workflows. Enforces budgets and mechanical constraints. Routes events to the Overseer. Manages pause/resume and crash recovery. Has no opinions. |
| `agent/` | Load agent definitions from TOML + .md prompt files. Validate schema. Resolve categories and permissions. |
| `tool/` | Tool registry. Each tool is a single Python object: name, description, parameters, execute. Granular constructors for file, git, exec, http operations. No bash tool. |
| `transport/` | LLM client via Provider protocol. Send messages to LLM APIs directly via httpx (no SDKs), stream responses, parse events, run the tool-call loop. Auto-retry transient API errors with an explicit required policy. |
| `verify/` | Run verification after each step. Ordered chains of Python callables (mechanical gates, cheapest first, short-circuit on failure), then verification agents (semantic checks with structured verdicts). |
| `notepad/` | Append-only cross-agent context sharing. Workers append learnings, decisions, issues. PG-backed via the trace schema. No overwrites. |
| `session/` | Session lifecycle management wrapping transport. Track session IDs for resumption. Track token counts. Conversation history in PG enables cross-restart resumption. |
| `trace/` | Single owner of the PostgreSQL schema. Tables: events, step results (per-attempt), transcripts, inbox items, notepad entries, config snapshots, workflow/step state. REVOKE UPDATE/DELETE on immutable tables. LISTEN/NOTIFY for cross-process observers. UUIDv7 primary keys. |
| `services/` | Shared business logic consumed by all three frontends (Python API, CLI, MCP server). One service per domain: runs, inbox, trace queries, config, validation. |
| `cli/` | strictcli-based CLI. Thin frontend over the services layer. Commands: run inspection, trace queries, TOML validation, inbox list/respond, config dump. Agents are the primary users. |
| `mcp/` | MCP server exposing the public API as MCP tools. The human's interface via dashboard or conversational AI client. |

## PostgreSQL as Backbone

All persistent state lives in PostgreSQL. The `trace/` module is the single owner of the database schema.

- **Overseer memory**: decisions, constraints, assumptions, lessons, workflow status
- **Run state**: events, step results (per-attempt), transcripts, notepad entries
- **Inbox**: human inbox items with tags and four-status lifecycle
- **Config snapshots**: fully resolved configuration persisted at run start
- **Immutability enforcement**: `REVOKE UPDATE, DELETE` on events, notepad entries, and transcripts at the DB-role level
- **Cross-process observation**: `LISTEN/NOTIFY` fires on event inserts; the MCP server and any dashboard observe live runs without polling
- **Mutual exclusion**: advisory locks + heartbeat prevent two schedulers on one run
- **IDs**: UUIDv7 primary keys everywhere (time-ordered, index-friendly)

`db_url` is a required parameter. No default. Missing is a hard error.

Dependency: `asyncpg`.

## Budget Model

Budgets are denominated and enforced in **USD**. oxtra maintains an internal pricing table (per-model input/output/cache token rates). The consumer is never bothered with pricing.

- Per-workflow budgets set by the Overseer
- Per-step budgets optional
- Enforcement: the scheduler compares accumulated USD against budget limits
- Threshold crossing triggers the Overseer's `budget_decision` protocol
- Overseer calls are funded from the triggering workflow's budget

Reports track **both** in parallel:

- Raw per-type token counts (input, output, reasoning, cache_read, cache_write) -- provider-attested, for audit
- USD figures computed from the internal pricing table -- labeled best-effort

The pricing table is maintained by oxtra's developers, not the consumer. When provider prices change, oxtra publishes an update. Best-effort means: the table may lag real pricing briefly, but enforcement and reporting always agree with each other.

## Secrets Model

Agents often need credentials (API keys, tokens) but anything that enters a prompt is persisted forever in transcripts. oxtra solves this with indirection + scrubbing:

1. **Registration**: consumers pass `secrets={"NAME": "value"}` at run construction. Paste-friendly, no env-file ceremony.
2. **Indirection**: the LLM only ever sees `{{secret:NAME}}` placeholders. The executor substitutes real values into tool arguments immediately before `execute()`, so agents can use credentialed tools without seeing credentials.
3. **Scrubbing**: exact-match scrubbing of registered secret values in tool results (before the LLM sees them) and in all trace persistence. If an agent's tool accidentally surfaces a secret (e.g., an error message containing a key), the value is replaced with `{{secret:NAME}}` before it enters context or disk.
4. **Guarantee**: registered values provably never reach the model or the database.

## Write Safety

Four mechanisms, each killing a distinct failure class:

| Mechanism | Failure class |
|---|---|
| Atomic replace (temp + fsync + rename) | Torn/half-written files on crash |
| Per-path write queue | Interleaved concurrent writes; racy read-modify-replace in edit |
| Transient-only executor replay | Re-paying output tokens for OS hiccups (never for deterministic errors like hunk mismatch) |
| Stale-write detection | Silent lost updates between agents |

**Stale-write detection**: the executor tracks the content hash of each file at each agent's last read. `write`/`edit` on an existing file hard-errors if the agent has never read its current version. Under the per-path lock, before applying, the executor compares the file's current hash against the hash at the agent's last read. Mismatch = hard error ("file changed since you read it -- re-read and re-apply"). New files need no prior read.

## No-Truncation Design

oxtra never discards tool output. "Too big" only governs what enters the agent's context window, not what is persisted.

- **Full persistence**: every tool result is stored in full in the database
- **Small results** (under a consumer-configured threshold `r1`): returned to the agent in full
- **Large results**: return a **preview** (line count, first N lines, last N lines) with the note that `full=true` is available
- **Escalation guard**: `full=true` only works if the agent already received the preview for that path in the current session; the executor enforces this, so the agent must see what it's asking for before requesting the full content
- **Consumer-pluggable previewer**: `make_read_tool` accepts an optional `previewer` callable that replaces the default head/tail preview with domain-specific output (e.g., function signatures)
- The same persist+preview+escalate pattern applies to `exec` stdout, `http` bodies, and `grep` floods
- `r1` and `N` (preview line count) are required constructor parameters on applicable tools

## Services Architecture

One **services layer** consumed by three thin frontends:

- **Python API** -- the library's public surface; direct function calls
- **CLI** -- strictcli-based; agents are the primary users; commands for run inspection, inbox, config, validation
- **MCP server** -- exposes the same services as MCP tools; the human's interface via dashboard or conversational AI

Logic lives once in `services/`. The frontends are thin projections. This prevents behavior drift between the programmatic API and the CLI/MCP surfaces.

The **web dashboard** (with its conversational AI agent) lives as a **sibling project**, not inside oxtra core. It consumes the MCP server + LISTEN/NOTIFY.

## Design Axioms

Twelve hard rules. Each is mechanically enforced, not prompt-requested.

1. **Agents are data, not code.** TOML metadata + .md prompts. No factory functions, no classes, no lifecycle methods. An agent definition is a static document that the framework loads and interprets.

2. **Tools are single typed objects.** `{name, description, parameters, execute}` -- schema and implementation are one object. No decoupled registries where schema lives in TOML and implementation lives elsewhere.

3. **Categories abstract model names.** Agents and workflow steps reference intent strings ("quick", "deep", "visual"), never model names. A flat map resolves intent to model. No fallback chains. Missing category is a hard error.

4. **Permissions are whitelists.** Each agent declares which tools it can use. Everything else is mechanically absent -- the LLM never sees unlisted tools. Not a prompt instruction. No deny lists, no RBAC, no inheritance. This includes framework-provided tools like `spawn`, `consult`, and `notepad` -- they follow the same whitelist rules as any domain tool. There is no separate 'framework tool' category.

5. **Subagents cannot delegate -- enforced mechanically.** Worker agents spawned by a workflow step do not have access to the `spawn` tool. This prevents orchestration recursion. The constraint is in the tool list construction, not in the prompt.

6. **Two-tier delegation.** Two levels of agent invocation: `spawn` (full agent with write access, orchestrator-only) and `consult` (read-only agent for research, available to workers). Workers can research but cannot spawn other workers.

7. **Mandatory parameters for consequential choices.** No implicit defaults for provider selection, model choice, database URL, or execution mode. Missing values are hard errors, not silent defaults.

8. **Verification is mechanical, not requested.** After every step, verification runs automatically. The scheduler runs verification callables and agents -- not the agent's prompt. The agent cannot skip verification. Verification agents return structured verdicts enforced by a framework-defined schema. The scheduler invokes them via `consult`.

9. **PostgreSQL IPC + session resumption.** Cross-agent context via append-only notepad entries in the database. Session continuity via session IDs and conversation history persisted in PostgreSQL. No in-memory shared state between agents.

10. **Auto-continuation.** The scheduler refuses to stop while steps remain incomplete. If a step fails and retries are available, the Overseer decides strategy. If a step succeeds, the scheduler moves to the next. Only exhausted retries, explicit abort, or budget exhaustion stop execution.

11. **The Overseer is the only long-lived entity.** Agent steps are scoped and short-lived -- they get a task, do it, and report back within their context window. If an agent step can't finish within its context window, that's a decomposition problem. Only the Overseer receives session handoff when its context fills: summary + UUID for querying the full transcript via trace.

12. **Structured decisions, not free-form.** The Overseer makes decisions via typed protocols with closed output schemas. It picks from menus, never free-forms. If a situation doesn't match any registered protocol, it escalates to the human.

## Anti-Patterns

Eleven patterns to avoid, identified from analysis of oh-my-openagent (omo) and Superagent -- similar projects that got many things right but suffered from complexity creep.

1. **No config sprawl.** The configuration surface is: agent TOML files, workflow TOML files, one categories TOML file, and Python tool definitions. No 100-knob config objects with nested sections.

2. **No hook/middleware system.** Behavior is in the scheduler, not in interceptor chains. No lifecycle hooks, no plugin registry, no event bus.

3. **No built-in agents.** oxtra is a framework. It defines zero agents -- those are the user's domain. The framework provides loading, validation, and execution. The analyzer/implementor/auditor/fixer taxonomy from Superagent is documented as example agent definitions consumers can copy and adapt.

4. **No model routing complexity.** Category to model is a flat dictionary lookup. No fallback chains, no fuzzy matching, no availability checks, no provider resolution layers.

5. **No massive functions.** No module should have a function over ~100 lines. If it's getting big, decompose.

6. **No bloated background manager.** Async agent execution uses standard asyncio. One completion detection path, not three.

7. **No skill system.** Agents get their prompt from composable .md files. No skill loaders, mergers, MCP managers, or frontmatter parsers.

8. **No prompts in code.** All prompt text lives in .md files, never in Python strings. Python files contain logic, not prose.

9. **No feature flags.** Features are either shipped or not. No `experimental` config sections, no `enabled: false` defaults.

10. **No bash tool.** The framework ships no general-purpose shell tool. Instead, granular purpose-built tools (read, write, edit, git, exec, http, etc.) with typed parameters and mechanical scoping. A consumer who truly needs raw shell writes their own Tool in ten lines -- oxtra refuses to bless one.

11. **No LLM calls hidden inside tools.** Every LLM interaction flows through budgeted, visible, whitelisted channels (spawn, consult). No tools that internally spawn LLM subprocesses with confidence-threshold auto-caching.

## Human Inbox

The system never blocks on human input by default. Three narrow, explicitly-declared mechanisms handle cases where waiting is genuinely required:

1. **Default: assume-record-proceed.** The Overseer makes its best assumption, records it, drops a tagged item into the inbox, and keeps working. Assumptions are never rewound.

2. **Autonomy action-gating.** Irreversible actions (deploy, delete data, external sends) are mechanically forbidden below the configured autonomy level. They require an answered approval inbox item to proceed. Enforced by the scheduler's action-type mapping, not by the Overseer's discretion.

3. **Explicit gate steps.** When the Overseer judges a real checkpoint is needed (compliance sign-off, external event), it generates a gate step with a timeout and escalation path. Inbox items declare an event that fires on answer, so gates can await them via PG LISTEN/NOTIFY.

### Inbox Item Schema

Each inbox item has:

- The question
- Options considered
- Which option the Overseer assumed
- What work proceeds under that assumption
- What happens if the human picks differently
- Tags (framework auto-injects the protocol-derived tag; Overseer may add free-form ones; tags are display/filter metadata only, never drive mechanical behavior)

### Inbox Statuses

- `pending` -- waiting for human response
- `answered` -- human replied (if contradicting the assumption, the contradiction is flagged in the report; work is not rewound)
- `skipped` -- human explicitly declined (assumption permanently blessed)
- `expired` -- deadline passed without engagement (assumption stands; distinguished from `skipped` in the report)

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
allow = ["read", "list_dir", "grep", "glob", "http", "consult", "notepad"]
```

No `[permissions]` section -- permissions are expressed entirely through the `allow` whitelist. `spawn` is mechanically stripped from all spawned agents regardless of config, so it never needs to be mentioned. `consult` and `notepad` are listed explicitly because the agent should be able to research via read-only agents and record learnings for downstream workflow steps.

The prompt file (`researcher.md`) lives alongside the TOML file and contains the agent's system prompt. It can reference `{variable}` placeholders that are substituted at spawn time from workflow step variables.

### Workflow Definition

Workflows are generated by the Overseer, not hand-written. The format:

```toml
[workflow]
name = "process-data"
description = "Full processing workflow: research, generate, review"

[[steps]]
name = "research"
agent = "researcher"
task = "Investigate {target}: gather relevant pages, extract key content and structure."
variables = ["target", "work_dir"]
depends_on_previous = false
timeout = 300
verify = ["myproject.verify:research_complete"]

[[steps]]
name = "generate"
agent = "generator"
category = "deep"
task = "Generate output for {target} based on the research data in {work_dir}."
variables = ["target", "work_dir", "output_path"]
depends_on = ["research"]
timeout = 600
verify = ["myproject.verify:output_valid"]

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
verify = ["myproject.verify:review_passed"]
verify_agent = "code-reviewer"
verify_block_threshold = "minor"
```

Key points:
- Every step must declare dependencies explicitly via `depends_on` (list of step names) or `depends_on_previous` (boolean). Both missing is a hard error.
- `timeout` is required on every agent step (seconds). No default.
- `verify` is an ordered list of Python callable paths (`module:function`). Run in order, cheapest first, short-circuiting on first failure.
- `verify_agent` names a full agent definition for semantic verification (structured verdict). Requires `verify_block_threshold`.
- `verify_block_threshold` is required when `verify_agent` is set. One of `critical | major | minor | nit`. Findings at or above this severity fail the step.
- `category` on a step overrides the agent's default category for that invocation.
- `retry` sets the maximum number of retries. `retry_resume` is required when `retry > 0`.
- `retry_inject_failure` injects the full structured failure picture: structured verdict/VerifyResult, the agent's previous output, and the attempt number.
- `variables` declares which workflow variables the step needs. Missing variables at runtime are hard errors.

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
- **Tools** as Python tool objects, built from oxtra's constructors plus custom domain tools
- **Intent** as the starting point for the Overseer to generate workflows
- **Verification functions** (research_complete, output_valid, review_passed) as Python callables
- **Knowledge** (conventions, constraints, banned patterns) as .md and .toml files in a `knowledge/` directory

The consumer builds a tool registry from oxtra's tool constructors (`make_read_tool`, `make_write_tool`, `make_edit_tool`, `make_git_tool`, `make_exec_tool`, `make_http_tool`, `make_spawn_tool`, `make_consult_tool`, `make_notepad_tool`, etc.) plus any custom domain tools, then calls oxtra's Python API to run. oxtra handles the rest: agent loading, tool filtering, model selection, Overseer-driven workflow generation, step execution, verification, retries, notepad IPC, and session tracking.

### Mixed Workflow Example

A workflow that mixes function steps (fetch, normalize, unify) with agent steps (extract). Demonstrates function steps for deterministic work, structured output validation, `for_each` for batch processing, and retry with failure injection:

```toml
[workflow]
name = "etl-workflow"
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
verify = ["myproject.verify:extraction_valid"]

[[steps]]
name = "publish"
callable = "myproject.steps:write_output"
variables = ["data_dir"]
depends_on = ["extract"]
```

### Fan-Out from Agent Decomposition

A workflow where a planner agent decomposes a task into subtasks, then a worker agent executes each subtask independently. Demonstrates structured output flowing into `for_each` iteration, with verification and post-step actions:

```toml
[workflow]
name = "generate-components"
description = "Decompose a task into components, implement each, then integrate"

[[steps]]
name = "plan"
agent = "planner"
task = "Decompose this task into independent subtasks with clear file scopes:\n\n{goal}"
variables = ["goal"]
depends_on_previous = false
timeout = 300
output_schema = "schemas/subtask_list.json"
verify = ["myproject.verify:plan_valid"]

[[steps]]
name = "implement"
agent = "coder"
task = "Implement this subtask:\n\nGoal: {item.goal}\nScope: {item.scope}\nContext: {item.context}"
for_each = "plan_output"
for_each_abort_on_failure = false
variables = ["plan_output", "work_dir"]
depends_on = ["plan"]
timeout = 600
write_paths = ["{item.scope}"]
verify = ["myproject.verify:code_compiles"]
on_success = "myproject.actions:commit_changes"

[[steps]]
name = "review"
agent = "reviewer"
task = "Review the implementation for correctness and consistency."
variables = ["work_dir"]
depends_on = ["implement"]
timeout = 300
verify_agent = "code-reviewer"
verify_block_threshold = "major"
```
