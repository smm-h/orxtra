# orxt -- Design

## What orxt Is

A Python library for autonomous multi-agent AI workflows. You provide intent; orxt drives it to completion.

An **Overseer** (persistent LLM with action tools and structured memory) makes judgment calls, creates workflows, and monitors execution. A **Scheduler** (deterministic event loop) validates tasks, enforces checks, and manages the recursive task hierarchy. **Agent tasks** (scoped LLM calls) do the actual work -- writing code, running tests, producing output.

## Philosophy

Complexity if you need it, simplicity if you don't.

Every module is independently useful for a narrow purpose. Together they compose into a full autonomous agent orchestration system. A consumer wanting only a typed LLM client uses `orxt.transport`. One wanting deterministic workflow execution uses `orxt.scheduler`. The full system composes all sixteen.

### Structured Programming for AI Workflows

orxt declares unstructured agent orchestration harmful.

Unstructured vibe coding -- agents spawning agents ad-hoc, no verification boundaries, free-form delegation, no pre/post conditions -- is the `goto` of AI workflows. It produces unpredictable results, makes debugging impossible, and wastes budget on work that fails late.

orxt applies the structured programming theorem to AI workflows:

- **Sequence**: tasks declare dependencies. Task B runs after task A.
- **Selection**: pre-checks gate entry. Post-checks gate exit. Failure branches to the parent.
- **Iteration**: failed post-checks let the agent retry. Escalation after exhaustion.
- **Nesting**: tasks contain subtasks. Workflows are tasks. Runs are tasks. No depth limit.

Every piece of work is a task with explicit boundaries (`start_task` / `end_task`), entry conditions (pre-checks), and exit conditions (post-checks). Tasks nest recursively. Failure propagates up the hierarchy. The parent decides what to do.

### SPT Mapping

| SPT Construct | In Programming | In orxt |
|---|---|---|
| Sequence | `a(); b(); c();` | Task dependencies: `depends_on` |
| Selection | `if (cond) { a() } else { b() }` | Pre-checks gate entry. Post-checks gate exit. Failure branches to parent. |
| Iteration (for) | `for item in list: f(item)` | `for_each`: same task spec runs N times with different `{item}` data |
| Iteration (while) | `while !done: retry()` | Post-check failure -> agent fixes -> `end_task` again |
| Block scope | `{ ... }` | `start_task` / `end_task` boundaries |
| Nesting | Functions calling functions | Tasks containing subtasks (`create_task`, `create_workflow`) |
| Variable scope | Local variables | Task variables, `write_paths` scoping |
| Exception propagation | `try/catch`, stack unwinding | Escalation up the task hierarchy |
| Function call | `result = f(args)` | Task output propagation: `{task_a_output}` |
| Return value | `return result` | `end_task(message)` with structured output |

## Monorepo Structure

rlsbl monorepo with 16 sub-projects. Each has its own `pyproject.toml`, `DESIGN.md`, `src/orxt/<name>/`, and `tests/`.

```
orxt/
    .rlsbl-monorepo/workspace.toml
    schema/orxt.toml              # pgdesign database schema
    knowledge/                     # Consumer domain knowledge (.md and .toml)

    protocols/                     # Foundation: shared types and interfaces
    secrets/                       # Foundation: secret registry + scrubbing
    write-safety/                  # Foundation: write queue + stale detection
    transport/                     # Foundation: typed LLM client
    agent/                         # Foundation: TOML+md agent loader
    tool/                          # Foundation: tool registry + constructors
    verify/                        # Foundation: check runner (pre/post-check execution)
    trace/                         # Foundation: PG schema owner
    notepad/                       # Foundation: cross-agent IPC
    session/                       # Foundation: session lifecycle

    scheduler/                     # Orchestration: task executor

    overseer/                      # Intelligence: persistent LLM brain
    knowledge-module/              # Intelligence: cognee enrichment (experimental)

    services/                      # Interface: shared business logic
    cli/                           # Interface: strictcli CLI
    mcp/                           # Interface: MCP server
```

## Architecture Layers

| Layer | Sub-projects | Rule |
|---|---|---|
| Foundation | protocols, secrets, write-safety, transport, agent, tool, verify, trace, notepad, session | Zero intra-workspace deps (exceptions: notepad -> trace, session -> transport + trace, transport -> protocols, tool -> protocols + secrets + write-safety, trace -> secrets, verify -> protocols) |
| Orchestration | scheduler | Depends on foundation |
| Intelligence | overseer, knowledge-module | Depends on foundation (not orchestration -- shared protocols at the seam) |
| Interfaces | services, cli, mcp | Depends on orchestration + intelligence |

Higher layers can depend on lower layers. Lower layers cannot depend on higher layers. The Overseer and scheduler never import each other -- they share type definitions via the protocols module.

## The Task Model

A task is the universal unit of work. Tasks nest recursively. A run is the root task. A workflow is a task containing subtasks. A leaf task is executed by an agent.

### Task Lifecycle

Every task has:
- **Pre-checks**: Executions that must pass before `start_task` succeeds
- **Post-checks**: Executions that must pass before `end_task` succeeds
- **An executor**: an agent (LLM), a callable (Python function), or subtasks (nested decomposition)

An Execution is a script (Python callable), an agent (spawned via consult), or a workflow (recursive task tree). See `protocols/DESIGN.md`.

### Task Boundaries

Agents interact with tasks via tool calls:
- `start_task(task_id)` -- enter the task. Pre-checks run. If all pass, the task becomes active.
- `end_task(message)` -- complete the task. Auto-commits file changes, then post-checks run. If any fail, the agent is told why and can fix its work.
- `create_task(...)` -- create a concrete subtask within the current task.
- `create_workflow(...)` -- create a goal-oriented subtask within the current task.

All other tool calls require an active task. Calling any tool without an active task is a hard error (except `start_task` itself).

### Escalation

When an agent cannot satisfy post-checks:
1. Failure is packaged as an `EscalationPayload` (task name, failed checks, attempt count, agent summary)
2. Delivered to the parent task's agent
3. The parent decides: create a fix task, adjust constraints, escalate further, or abort
4. If escalation reaches the Overseer (root task), the Overseer handles it via action tools

### Structured Delegation

Agents delegate work by creating subtasks, not by spawning free-floating sub-agents:
- `create_task` -- concrete: "modify function X, update callers." A task agent executes it.
- `create_workflow` -- goal-oriented: "refactor auth to use JWT." A consumer-provided workflow agent decomposes it into subtasks.

Both create structured subtasks within the parent task. Subtasks have their own pre/post-checks. Failure escalates to the parent. Budget flows from parent to child.

For read-only research, agents use `consult` -- a read-only agent session. Consulted agents cannot write files, create tasks, or modify system state.

## PostgreSQL as Backbone

All persistent state lives in PostgreSQL. The `trace/` module is the single owner of the database schema.

- **Overseer memory**: decisions, constraints, assumptions, lessons, workflow status
- **Run state**: events, task results (per-attempt), transcripts, notepad entries
- **Inbox**: human inbox items with tags and five-status lifecycle
- **Config snapshots**: fully resolved configuration persisted at run start
- **Immutability enforcement**: `REVOKE UPDATE, DELETE` on events, notepad entries, and transcripts
- **Cross-process observation**: `LISTEN/NOTIFY` on event inserts
- **Mutual exclusion**: advisory locks + heartbeat per run
- **IDs**: UUIDv7 primary keys everywhere (pg-uuidv7 function, no v4 fallback)

`db_url` is a required parameter. No default. Missing is a hard error.

## Budget Model

Budgets are denominated and enforced in **USD**. orxt maintains an internal pricing table (per-model input/output/cache token rates). Budget is the natural depth limit for task nesting.

- Per-workflow and per-task budgets
- Enforcement: the scheduler compares accumulated USD against budget limits
- Threshold crossing sends a `BudgetThresholdCrossed` event to the Overseer
- Overseer calls are funded from the triggering workflow's budget

Reports track **both**:
- Raw per-type token counts (input, output, reasoning, cache_read, cache_write) -- provider-attested
- USD figures computed from the internal pricing table -- labeled best-effort

## Secrets Model

Agents often need credentials but anything that enters a prompt is persisted forever in transcripts. orxt solves this with indirection + scrubbing:

1. **Registration**: consumers pass `secrets={"NAME": "value"}` at run construction
2. **Indirection**: the LLM only sees `{{secret:NAME}}` placeholders. Real values substituted into tool arguments immediately before `execute()`
3. **Scrubbing**: exact-match scrubbing of registered secret values in tool results and trace persistence
4. **Guarantee**: registered values provably never reach the model or the database

## Write Safety

| Mechanism | Failure class |
|---|---|
| Atomic replace (temp + fsync + rename) | Torn/half-written files on crash |
| Per-path write queue | Interleaved concurrent writes |
| Transient-only executor replay | Re-paying output tokens for OS hiccups |
| Stale-write detection | Silent lost updates between agents |

## No-Truncation Design

orxt never discards tool output. "Too big" only governs what enters the agent's context window, not what is persisted. Small results returned in full. Large results return a preview with opt-in `full=true` retrieval.

## Services Architecture

One **services layer** consumed by three thin frontends:

- **Python API** -- direct function calls. The primary programmatic interface.
- **CLI** -- strictcli-based. Agents are the primary users. Also supports config-file-based run start.
- **MCP server** -- exposes the same services as MCP tools. The human's interface via dashboard or AI client.

Logic lives once in `services/`. The frontends are thin projections.

## Human Inbox

The system never blocks on human input by default. Three mechanisms:

1. **Default: assume-record-proceed.** The Overseer makes its best assumption, records it via `record_assumption`, drops a tagged item into the inbox via `create_inbox_item`, and keeps working.

2. **Autonomy action-gating.** Irreversible actions are mechanically forbidden below the configured autonomy level. They require an answered approval inbox item to proceed.

3. **Wait-for tasks.** The Overseer creates wait-for tasks with a timeout and escalation path. Wait-for tasks await named events via PG LISTEN/NOTIFY. External systems fire events via the services API.

### Inbox Item Schema

Each inbox item has: the question, options considered, which option the Overseer assumed, what work proceeds under that assumption, what happens if the human picks differently, and tags.

### Inbox Statuses

`pending` -> `answered` | `skipped` | `expired` | `rejected`.

`rejected` means the human found the options insufficient. The Overseer must re-investigate via tools and create a new inbox item with better options. The new item must include the most correct solution regardless of effort.

## Design Axioms

Sixteen hard rules. Each is mechanically enforced.

1. **Agents are data, not code.** TOML metadata + .md prompts. No factory functions, no classes, no lifecycle methods.

2. **Tools are single typed objects.** `{name, description, parameters, execute}` -- schema and implementation are one object.

3. **Categories abstract model names.** Agents reference intent strings ("quick", "deep"), never model names. A flat map resolves intent to model. No fallback chains. Missing category is a hard error.

4. **Permissions are whitelists.** Each agent declares which tools it can use. Everything else is mechanically absent.

5. **Task creation is structured.** Agents create subtasks via `create_task` (concrete) or `create_workflow` (goal-oriented). Subtasks nest within the parent task, have their own pre/post-checks, and failure escalates to the parent. There is no unstructured delegation. No free-floating sub-agents.

6. **Two delegation modes.** `create_task` / `create_workflow` for write-capable structured delegation. `consult` for read-only research. No other delegation mechanism.

7. **Mandatory parameters for consequential choices.** No implicit defaults for provider, model, database URL, timeout, context refinement, or retry behavior. Missing values are hard errors.

8. **Checks are mechanical, not requested.** Pre-checks run at `start_task`. Post-checks run at `end_task`. The agent cannot skip checks. When post-checks fail, the agent is told why and can fix its work. When the agent cannot fix it, failure escalates to the parent. Checks are Executions: scripts, agents, or workflows.

9. **PostgreSQL IPC + session resumption.** Cross-agent context via append-only notepad entries. Session continuity via session IDs and conversation history persisted in PostgreSQL.

10. **Auto-continuation.** The scheduler refuses to stop while tasks remain incomplete. Failed tasks escalate to parents. Only exhausted retries, explicit abort, or budget exhaustion stop execution.

11. **The Overseer is the only long-lived entity.** Task agents are scoped and short-lived. If a task agent cannot finish within its context window, that is a decomposition problem. Only the Overseer receives session handoff.

12. **The Overseer acts via tools.** The Overseer's influence is through action tool calls (create_workflow, add_constraint, record_decision, etc.), not free-form text. Tools enforce structure. Every action is recorded in the trace.

13. **Modules compose via layer boundaries.** Foundation modules expose stable interfaces. Higher-layer modules depend on lower-layer concrete types. The Overseer and scheduler share type definitions via the protocols module but never import each other.

14. **Disciplined tools enforce conventions by construction.** Git mutations wrap safegit. File deletion wraps saferm. There is no bash tool. Agents cannot bypass disciplined tools.

15. **Correctness over convenience.** The Overseer and all agent-type checks prefer the most correct solution regardless of effort. LLMs default to easy answers and overestimate effort to avoid work. orxt counteracts this at every level: the Overseer's system prompt, inbox item options, postcheck agent prompts. Never defer work. Never recommend the easy path when a more correct one exists.

16. **Structural advice, not structural mutation.** The scheduler detects structural improvements in the task tree (front-loading, reordering, parallelization) but never applies them autonomously. It surfaces them as advisory messages to the owning agent. The agent evaluates and decides. Mechanical analysis informs; intelligence decides.

## Scope

orxt is the engine for a single project's autonomous workflows. Cross-project coordination (managing releases across a project ecosystem, filing todos in other projects, orchestrating multi-project workflows) is out of scope. That responsibility belongs to a consumer of orxt -- a domain-specific orchestrator that drives orxt instances across projects.

## Anti-Patterns

1. **No config sprawl.** The configuration surface is: agent TOML files, workflow TOML files, one categories TOML file, and Python tool definitions.

2. **No hook/middleware system.** Behavior is in the scheduler, not in interceptor chains.

3. **No built-in agents.** orxt is a framework. It defines zero agents.

4. **No model routing complexity.** Category to model is a flat dictionary lookup.

5. **No massive functions.** No module should have a function over ~100 lines.

6. **No bloated background manager.** Async agent execution uses standard asyncio.

7. **No skill system.** Agents get their prompt from composable .md files.

8. **No prompts in code.** All prompt text lives in .md files, never in Python strings.

9. **No feature flags.** Optional subsystems (Overseer, verification, cognee) are activated by providing their config object. Presence of config IS the signal.

10. **No bash tool.** Granular purpose-built tools with typed parameters.

11. **No LLM calls hidden inside tools.** Every LLM interaction flows through budgeted, visible, whitelisted channels (create_task, create_workflow, consult).

12. **No unstructured delegation.** No free-floating sub-agents. All delegation is structured: subtasks within tasks, with checks and escalation. No `goto` -- only structured control flow.

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
allow = ["read", "list_dir", "grep", "glob", "http", "consult", "notepad", "start_task", "end_task"]
```

### Workflow Definition

Workflows are generated by the Overseer or defined in TOML. Dependencies are declared at the end, after all tasks are listed -- this lets the LLM see all task names before declaring the dependency graph.

```toml
[workflow]
name = "process-data"
description = "Full processing workflow: research, generate, review"

[[tasks]]
name = "research"
agent = "researcher"
task_prompt = "Investigate {target}: gather relevant pages, extract key content."
variables = ["target", "work_dir"]
timeout = 300
context_refinement = true

[tasks.postchecks]
scripts = ["myproject.verify:research_complete"]

[[tasks]]
name = "generate"
agent = "generator"
category = "deep"
task_prompt = "Generate output for {target} based on the research data in {work_dir}."
variables = ["target", "work_dir", "output_path"]
timeout = 600
context_refinement = true

[tasks.postchecks]
scripts = ["myproject.verify:output_valid"]

[[tasks]]
name = "review"
agent = "reviewer"
task_prompt = "Run the test harness against the output at {output_path}."
variables = ["target", "output_path"]
timeout = 300
retry = 5
retry_resume = true
retry_inject_failure = true
context_refinement = false

[tasks.postchecks]
scripts = ["myproject.verify:review_passed"]
agents = [{agent = "code-reviewer", task = "Review the output", block_threshold = "minor"}]

[dependencies]
generate = ["research"]
review = ["generate"]
# Tasks not listed have no dependencies and can run immediately
```

### Categories

```toml
# categories.toml
[categories]
quick = "anthropic/claude-haiku-4-5"
standard = "anthropic/claude-sonnet-4-6"
deep = "anthropic/claude-opus-4-6"
visual = "google/gemini-2.5-flash"
```

### Tool-Less Agent

```toml
# agents/extractor.toml
[agent]
name = "extractor"
description = "Extracts structured information from unstructured text"
prompt = "extractor.md"
category = "standard"

[tools]
allow = ["start_task", "end_task"]
```

Agents with only task lifecycle tools are a first-class pattern. The agent receives the task prompt and produces text output. No other tools are offered to the LLM. This is appropriate for classification, extraction, and summarization.

## What orxt Is NOT

- **Not a plugin for an existing agent runtime.** orxt is standalone.
- **Not a prompt engineering framework.** It loads prompts from files and passes them through.
- **Not a model router with fallback chains.** Category-to-model is a flat dictionary lookup.
- **Not a plugin system.** Consumers extend orxt via Tool objects, Executions, and constructor parameters.
