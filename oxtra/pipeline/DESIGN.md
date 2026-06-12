# Pipeline Module Design

The pipeline module is the heart of oxtra. Pipelines define and execute multi-step agent workflows.

## Core Concept

A pipeline is a declarative TOML file that defines a sequence of steps. Each step names an agent, provides a task prompt, and specifies dependencies, timeout, verification, and retry policy. The pipeline executor reads the file and runs it mechanically.

## Pipeline Definition Format (TOML)

```toml
[pipeline]
name = "process-data"
description = "Full processing pipeline: research, generate, review"

[[steps]]
name = "research"
agent = "researcher"
task = "Investigate {target}: gather relevant pages and extract key content."
variables = ["target", "work_dir"]
depends_on_previous = false
timeout = 300
verify = "myproject.verify:research_complete"

[[steps]]
name = "generate"
agent = "generator"
category = "deep"
task = "Generate output for {target} based on research data in {work_dir}."
variables = ["target", "work_dir", "output_path"]
depends_on = ["research"]
timeout = 600
verify = "myproject.verify:output_valid"

[[steps]]
name = "review"
agent = "reviewer"
task = "Run test harness against output at {output_path}. Report pass/fail for each check."
variables = ["target", "output_path"]
depends_on = ["generate"]
timeout = 300
retry = 5
retry_resume = true
retry_inject_failure = true
verify = "myproject.verify:review_passed"
verify_agent = "reviewer"
```

## Step Schema

Each `[[steps]]` entry has these fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique step identifier |
| `agent` | string | yes* | Agent name (must exist in loaded agents). Mutually exclusive with `callable`. |
| `task` | string | yes* | Task prompt. `{variable}` placeholders substituted from runtime variables. Mutually exclusive with `callable`. |
| `callable` | string | yes* | Python callable path (e.g., `"myproject.steps:fetch_data"`). Mutually exclusive with `agent` + `task`. |
| `variables` | array of strings | yes | Variable names this step needs. Hard error if any are missing at runtime. |
| `depends_on` | array of strings | yes** | Step names that must complete successfully before this step runs. Mutually exclusive with `depends_on_previous`. |
| `depends_on_previous` | boolean | yes** | If true, depends on the immediately preceding step. If false, no dependencies. Mutually exclusive with `depends_on`. |
| `timeout` | integer | yes (agent steps) | Max wall-clock seconds before the executor kills the session. No default. Not applicable to function steps. |
| `category` | string | no | Override the agent's default category for this step. Agent steps only. |
| `retry` | integer | no | Max retry count on failure. Default: 0 (no retries). |
| `retry_resume` | boolean | conditional | Required if `retry > 0`. True = continue existing session on retry. False = start fresh session. |
| `retry_inject_failure` | boolean | no | If true, inject the failure details from the previous attempt into the retry prompt. Default: false. |
| `verify` | string | no | Python callable path (e.g., `"myproject.verify:check_fn"`) for mechanical verification. Called after the step completes. |
| `verify_agent` | string | no | Agent name for semantic verification. Must be a full agent definition (TOML + .md). Spawned as a read-only consult after mechanical verification passes. Agent steps only. |
| `for_each` | string | no | Variable name that resolves to a list. The step executes once per item. |
| `for_each_abort_on_failure` | boolean | conditional | Required if `for_each` is set. True = abort pipeline on any iteration failure. False = continue with partial results. |
| `output_schema` | string | no | Path to a JSON Schema file (relative to the pipeline TOML file). Validated JSON is available as `{step_name}_output`. Agent steps only. |

*A step must declare either `agent` + `task` (agent step) or `callable` (function step), never both.

**Every step must declare either `depends_on` or `depends_on_previous`. Both missing is a hard error. Both present is a hard error.

## Dependencies and Parallelism

The executor builds a dependency graph from `depends_on` and `depends_on_previous` declarations. Steps with no dependency between them run in parallel via asyncio. This is computed from the graph, not configured -- if two steps share the same dependency and neither depends on the other, they run concurrently.

`for_each` iterations are also parallel. Iterations are independent by definition -- item 17 has no relationship to item 18.

The executor performs a topological sort of the dependency graph at load time. Cycles are hard errors.

## Function Steps

A function step runs a Python callable instead of an LLM agent. This enables mixed pipelines where some steps are LLM-driven and others are pure Python -- data fetching, file transformation, validation, or any deterministic computation.

A step declares `callable` instead of `agent` + `task`. The two are mutually exclusive: a step is either an agent step or a function step. The callable is a dotted Python path with colon separator, the same format used by verification callables: `"myproject.steps:fetch_data"`.

The callable signature:

```python
async def fetch_data(ctx: StepContext) -> StepResult
```

`StepContext` contains `variables` (the resolved variable map), `run_dir` (path to the run directory), `step_name`, and `attempt` (current attempt number, starting at 1). `StepResult` is `{passed: bool, message: str, output: dict | None}`. The `output` dict, if present, is merged into the pipeline's variable map and available to subsequent steps. If the output dict contains a key that already exists in the variable map, the pipeline aborts with a hard error (variable name collision).

The executor runs the callable directly -- no transport, no session, no tools. It's a Python function call.

Function steps support: `variables`, `depends_on`/`depends_on_previous`, `retry`, `retry_inject_failure`, `verify` (mechanical verification), `for_each`.

Function steps do not support: `category` (no LLM to route), `timeout` (use the callable's own timeout), `verify_agent` (no agent output to verify semantically). These fields are hard errors on function steps.

Notepad injection does not apply -- there is no LLM prompt to inject into.

```toml
[[steps]]
name = "fetch-data"
callable = "myproject.steps:fetch_data"
variables = ["data_dir"]
depends_on_previous = false
verify = "myproject.verify:raw_data_exists"

[[steps]]
name = "normalize"
callable = "myproject.steps:normalize_all"
variables = ["data_dir"]
depends_on = ["fetch-data"]

[[steps]]
name = "extract"
agent = "extractor"
task = "Extract key fields and a summary from this document:\n\n{content}"
variables = ["content", "title", "missing_fields"]
depends_on = ["normalize"]
timeout = 120
category = "standard"
verify = "myproject.verify:extraction_valid"
```

## Batch Iteration

A step can declare `for_each` to execute multiple times over a list variable.

`for_each = "variable_name"` names a variable that must resolve to a list at runtime. The step executes once per item in that list. Each iteration gets the current item injected as `{item}` in the task prompt (for agent steps) or as `ctx.item` on the `StepContext` (for function steps). The original list variable remains available as `{variable_name}` (the full list).

Iterations run in parallel via asyncio. All iterations complete (or fail) before downstream steps execute.

Results are collected into a list variable named `{step_name}_results`, available to downstream steps.

When `for_each_abort_on_failure = false`, `{step_name}_results` contains all items: successes have their output, failures have error details. Downstream steps see the full list and decide what to do. When `for_each_abort_on_failure = true`, any iteration failure aborts the pipeline immediately.

A step with `for_each` supports all other fields: `retry` (per-iteration), `verify` (per-iteration), `depends_on`, `timeout` (per-iteration), `category` override (agent steps), `callable` (function steps).

When a step has both `for_each` and `output_schema`, each iteration's output is validated against the schema independently. Results are collected as a list in `{step_name}_output`. Failed validations trigger per-iteration retry.

```toml
[[steps]]
name = "extract-document"
agent = "extractor"
task = "Extract key fields and a summary from this document:\n\nTitle: {item.title}\nContent:\n{item.body}"
for_each = "documents_to_process"
for_each_abort_on_failure = false
variables = ["documents_to_process"]
depends_on = ["normalize"]
timeout = 120
category = "standard"
output_schema = "schemas/extraction.json"
retry = 2
retry_resume = false
retry_inject_failure = true
verify = "myproject.verify:extraction_valid"
```

## Structured Output

A pipeline step can declare that it expects structured JSON output from the agent. The executor validates the output against a JSON Schema and retries on parse or validation failure.

`output_schema` on a step is a path to a JSON Schema file, relative to the pipeline TOML file. It is optional -- steps without it treat the agent's text output as an opaque string. `output_schema` is only for agent steps, not function steps (function steps return structured data via `StepResult.output`).

After the agent completes and verification passes, the executor extracts JSON from the agent's text output:

1. Tries to parse the entire output as JSON
2. If that fails, looks for a JSON block inside markdown fences (`` ```json ... ``` ``)
3. If that fails, the step fails (triggering retry with the parse error injected if `retry_inject_failure` is set)

The parsed JSON is validated against the JSON Schema. Validation failure is a step failure (triggering retry if retries remain). The validated JSON is available to downstream steps as `{step_name}_output`.

## Pipeline Executor

The executor is the core runtime. It invokes agents via the `spawn` tool -- the same tool available to orchestrator-level agents. This is the single code path for all agent invocation.

1. **Loads the pipeline TOML** and validates the schema.
2. **Builds the dependency graph** -- topological sort of steps. Cycles are hard errors.
3. **Creates a run directory** -- `{data_dir}/runs/{pipeline_name}/{timestamp}/` for trace data, notepad files, and artifacts.
4. **Executes steps in dependency order** (parallel where the graph allows):
   - Substitutes `{variable}` placeholders in the task prompt
   - Loads the agent definition (TOML + .md prompt)
   - Resolves category to model
   - Filters tool registry by agent permissions
   - Injects notepad context (appends current notepad learnings to the agent's prompt)
   - Invokes the agent via `spawn`
   - Enforces timeout -- kills the session if wall-clock time exceeds `timeout`
   - Runs mechanical verification (if `verify` is set)
   - Runs verification agent (if `verify_agent` is set and mechanical verification passed)
   - On failure with retries remaining: resume or start fresh per `retry_resume`, inject failure context if `retry_inject_failure`
   - On failure with no retries: mark step as failed, abort pipeline (no partial success)
   - On success: record result, move to next step
5. **Auto-continuation** -- the executor never stops voluntarily while steps remain. Only terminal failure (exhausted retries) or explicit abort stops execution. There is no "pause" or "skip" mechanism.

## Cancellation

The executor supports two cancellation mechanisms:

- **asyncio cancellation** -- the caller cancels the executor's task via standard asyncio task cancellation. The executor catches `CancelledError`, cleans up active sessions, and writes partial results to the run directory.
- **`abort()` method** -- the executor object exposes an `abort()` method the caller can invoke from another task or thread. This sets an internal flag checked between steps and triggers asyncio cancellation. Useful for budget enforcement or user-initiated abort.

## Run Directory

The executor delegates all persistence to the `trace/` module. See `trace/DESIGN.md` for the run directory structure, artifact formats, and query API. The pipeline executor calls `trace.write_step_result()` after each step and `trace.write_pipeline_result()` at the end.

## Crash Recovery

The executor persists step results via `trace/` as each step completes. On restart, the caller can read previously completed step results from a prior run directory (via `trace.read_step_result()`) and pass them to the executor as `completed_steps` to skip already-finished steps. This is not automatic resume -- the caller decides what to reuse.

## Key Design Decisions

**No conditional branching.** Pipeline steps don't have `if` conditions. Every step either runs (when its dependencies succeed) or doesn't (when a dependency fails and aborts the pipeline). Conditional logic belongs in the verification function, not the pipeline definition. If you need "skip this step if X already exists," that's a verify function on a prior step that checks state, or a verify function on the step itself that short-circuits.

**No step-level model override via variables.** The model is determined by category resolution, not by a variable in the task prompt. You can override category per-step (which changes the model), but you cannot pass a model string as a variable.

**Retry with failure context.** When `retry_inject_failure = true` and a step fails, the retry prompt gets the original task plus a section like:

```
Previous attempt failed. Failure details:
{verification_error_output}

Fix the issues and try again.
```

This is mechanical -- the executor builds the retry prompt, not the agent.

**Abort on failure, no partial success.** If a step fails after exhausting retries, the entire pipeline aborts. There is no "continue with remaining steps" mode. A pipeline either succeeds completely or fails with a clear failure point. This prevents cascading errors from incomplete prerequisites.

**Variable name collisions are hard errors.** When a function step's output dict contains a key that already exists in the pipeline variable map, the pipeline aborts. This prevents silent overwrites.

## Cost Tracking

- Each step records: `input_tokens`, `output_tokens`, `cost_usd`, `retries_used`
- Pipeline total: sum of all step costs
- Available after pipeline completion or failure as a `PipelineResult` object
- No budget enforcement in the pipeline itself -- the caller uses `abort()` to enforce budgets

## Files

| File | Contents |
|---|---|
| `_types.py` | `StepContext`, `StepResult` (function step return type), `PipelineConfig` (parsed pipeline TOML). Step schema field definitions. |
| `_loader.py` | `load_pipeline(path)` -- reads pipeline TOML, validates schema (required fields, mutual exclusivity, conditional requirements), returns `PipelineConfig`. |
| `_graph.py` | Dependency graph construction from `depends_on`/`depends_on_previous`. Topological sort. Cycle detection. Identifies parallelizable step groups. |
| `_executor.py` | `PipelineExecutor` class. The core runtime: step execution loop, spawn invocation, timeout enforcement, retry logic, verification dispatch, notepad injection, asyncio parallelism, `abort()` method. Delegates persistence to `trace/`. |

## What This Module Does NOT Do

- Does not define agents or tools (those are loaded externally)
- Does not manage transport connections (uses `session/`)
- Does not implement model selection logic (uses `agent/` category resolution)
- Does not provide a CLI or TUI for running pipelines
- Does not support unbounded iteration -- `for_each` requires a finite list known at step start time
