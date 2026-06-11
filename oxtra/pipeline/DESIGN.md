# Pipeline Module Design

The pipeline module is the heart of oxtra. Pipelines define and execute multi-step agent workflows.

## Core Concept

A pipeline is a declarative TOML file that defines a sequence of steps. Each step names an agent, provides a task prompt, and optionally specifies verification, retry policy, and dependencies. The pipeline executor reads the file and runs it mechanically.

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
verify = "myproject.verify:research_complete"

[[steps]]
name = "generate"
agent = "generator"
category = "deep"
task = "Generate output for {target} based on research data in {work_dir}."
variables = ["target", "work_dir", "output_path"]
depends_on = ["research"]
verify = "myproject.verify:output_valid"

[[steps]]
name = "review"
agent = "reviewer"
task = "Run test harness against output at {output_path}. Report pass/fail for each check."
variables = ["target", "output_path"]
depends_on = ["generate"]
retry = 5
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
| `category` | string | no | Override the agent's default category for this step. Agent steps only. |
| `depends_on` | array of strings | no | Step names that must complete successfully before this step runs. Default: previous step (sequential). |
| `retry` | integer | no | Max retry count on failure. Default: 0 (no retries). |
| `retry_inject_failure` | boolean | no | If true, inject the failure details from the previous attempt into the retry prompt. Default: false. |
| `verify` | string | no | Python callable path (e.g., `"myproject.verify:check_fn"`) for mechanical verification. Called after the step completes. |
| `verify_agent` | string | no | Agent name for semantic verification. Spawned as a read-only consult after mechanical verification passes. Agent steps only. |
| `for_each` | string | no | Variable name that resolves to a list. The step executes once per item. |
| `output_schema` | string | no | Path to a JSON Schema file (relative to the pipeline TOML file). Validated JSON is available as `{step_name}_output`. Agent steps only. |

*A step must declare either `agent` + `task` (agent step) or `callable` (function step), never both.

## Function Steps

A function step runs a Python callable instead of an LLM agent. This enables mixed pipelines where some steps are LLM-driven and others are pure Python -- data fetching, file transformation, validation, or any deterministic computation.

A step declares `callable` instead of `agent` + `task`. The two are mutually exclusive: a step is either an agent step or a function step. The callable is a dotted Python path with colon separator, the same format used by verification callables: `"myproject.steps:fetch_data"`.

The callable signature:

```python
async def fetch_data(ctx: StepContext) -> StepResult
```

`StepContext` contains `variables` (the resolved variable map), `run_dir` (path to the run directory), `step_name`, and `attempt` (current attempt number, starting at 1). `StepResult` is `{passed: bool, message: str, output: dict | None}`. The `output` dict, if present, is merged into the pipeline's variable map and available to subsequent steps.

The executor runs the callable directly -- no transport, no session, no tools. It's a Python function call.

Function steps support: `variables`, `depends_on`, `retry`, `retry_inject_failure`, `verify` (mechanical verification), `for_each`.

Function steps do not support: `category` (no LLM to route), `verify_agent` (no agent output to verify semantically). These fields are hard errors on function steps.

Notepad injection does not apply -- there is no LLM prompt to inject into.

```toml
[[steps]]
name = "fetch-data"
callable = "myproject.steps:fetch_data"
variables = ["data_dir"]
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
category = "standard"
verify = "myproject.verify:extraction_valid"
```

## Batch Iteration

A step can declare `for_each` to execute multiple times over a list variable.

`for_each = "variable_name"` names a variable that must resolve to a list at runtime. The step executes once per item in that list. Each iteration gets the current item injected as `{item}` in the task prompt (for agent steps) or as `ctx.item` on the `StepContext` (for function steps). The original list variable remains available as `{variable_name}` (the full list).

Iterations are independent -- one failing does not abort others. This is a deliberate departure from the pipeline's usual abort-on-failure rule. Unlike sequential steps where a failure means broken prerequisites, list items are independent units of work. One malformed document should not block 43 good ones.

Results are collected into a list variable named `{step_name}_results`, available to downstream steps.

Iterations may run in parallel if the executor supports it. This is an implementation detail, not guaranteed by the spec. The only guarantee is that all iterations complete (or fail) before downstream steps execute.

A step with `for_each` supports all other fields: `retry` (per-iteration), `verify` (per-iteration), `depends_on`, `category` override (agent steps), `callable` (function steps).

```toml
[[steps]]
name = "extract-document"
agent = "extractor"
task = "Extract key fields and a summary from this document:\n\nTitle: {item.title}\nContent:\n{item.body}"
for_each = "documents_to_process"
variables = ["documents_to_process"]
category = "standard"
retry = 2
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

```toml
[[steps]]
name = "enrich-meeting"
agent = "enricher"
task = "Extract structured data from this transcript..."
variables = ["transcript"]
category = "standard"
output_schema = "schemas/enrichment.json"
retry = 3
retry_inject_failure = true
```

## Pipeline Executor

The executor is the core runtime. It:

1. **Loads the pipeline TOML** and validates the schema.
2. **Resolves dependencies** -- topological sort of steps based on `depends_on`. Cycles are hard errors.
3. **Creates a run directory** -- `{data_dir}/runs/{pipeline_name}/{timestamp}/` for notepad files, logs, artifacts.
4. **Executes steps in dependency order**:
   - Substitutes `{variable}` placeholders in the task prompt
   - Loads the agent definition (TOML + .md prompt)
   - Resolves category to model
   - Filters tool registry by agent permissions
   - Injects notepad context (appends current notepad learnings to the agent's prompt)
   - Sends the task to the transport via a session
   - Waits for completion
   - Runs mechanical verification (if `verify` is set)
   - Runs verification agent (if `verify_agent` is set and mechanical verification passed)
   - On failure with retries remaining: inject failure context if `retry_inject_failure`, re-run step
   - On failure with no retries: mark step as failed, abort pipeline (no partial success)
   - On success: record result, move to next step
5. **Auto-continuation** -- the executor never stops voluntarily while steps remain. Only terminal failure (exhausted retries) or explicit abort stops execution. There is no "pause" or "skip" mechanism.

## Key Design Decisions

**Sequential by default, parallel by dependency.** Steps without explicit `depends_on` depend on the previous step (sequential chain). Steps that share the same dependency can run in parallel. This is derived from the TOML, not a runtime decision.

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

**for_each iterations are independent.** When a step uses `for_each`, individual iteration failures do not abort other iterations. This is an intentional exception to the abort-on-failure rule. Pipeline steps have causal dependencies -- step B needs step A's output. List items don't -- item 17 has no relationship to item 18. Aborting 43 successful enrichments because one transcript was malformed would discard valid work for no reason.

## Cost Tracking

- Each step records: `input_tokens`, `output_tokens`, `cost_usd`, `retries_used`
- Pipeline total: sum of all step costs
- Available after pipeline completion or failure as a `PipelineResult` object
- No budget enforcement in the pipeline itself -- that's the caller's responsibility

## What This Module Does NOT Do

- Does not define agents or tools (those are loaded externally)
- Does not manage transport connections (uses `session/`)
- Does not implement model selection logic (uses `agent/` category resolution)
- Does not provide a CLI or TUI for running pipelines
- Does not persist pipeline state for crash recovery (a run either completes or fails)
- Does not support unbounded iteration -- `for_each` requires a finite list known at step start time
