---
title: Configuration Reference
description: TOML format reference for agent definitions, workflow definitions, category mappings, run configuration, knowledge files, data tool definitions, and environment variables.
---

# Configuration Reference

All configuration files use TOML format with a required integer `format_version = 1` at the top level. Documents are validated at the load boundary by strictspec-generated validators -- malformed documents produce hard errors with diagnostic paths, never silent degradation.

## agent.toml

Agent definitions live in individual `.toml` files. Each file defines one agent with its identity, model routing, tool permissions, and optional inline tool declarations. Load with `orxtra.agent.load_agent(path)` or batch-load a directory with `orxtra.agent.load_agents(directory)`.

Prompt text lives in a separate `.md` file referenced by the `prompt` field. The loader resolves the path relative to the TOML file, reads the markdown, and performs include resolution via `orxtra.compose`.

### `[agent]` section

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique agent identifier. Must be non-empty. |
| `description` | string | yes | Human-readable description. Must be non-empty. |
| `prompt` | string | yes | Path to the `.md` prompt file, relative to this TOML file. Must be non-empty. |
| `category` | string | no | Category name for model routing via `categories.toml`. Mutually exclusive with `provider`/`model`. |
| `provider` | string | no | LLM provider name (e.g. `"anthropic"`). Must be set together with `model`. Mutually exclusive with `category`. |
| `model` | string | no | LLM model identifier (e.g. `"claude-sonnet-4-6"`). Must be set together with `provider`. Mutually exclusive with `category`. |
| `budget` | number | no | Maximum spend in USD for this agent. Must be >= 0. |
| `write_paths` | array of string | no | Allowed write paths for file operations. |
| `timeout` | integer | no | Agent-level timeout in seconds. Must be >= 1. |

**Routing constraints:** Exactly one routing form must be present:

- `category` alone (resolved against `categories.toml` at runtime), OR
- `provider` AND `model` together (direct provider/model specification)

Setting both `category` and `provider`/`model` is a validation error. Omitting both is also an error.

### `[tools]` section

| Field | Type | Required | Description |
|---|---|---|---|
| `allow` | array of string | yes | Tool names or glob patterns the agent may use (e.g. `["read", "write", "custom.*"]`). |
| `deferred` | array of string | no | Tool names to defer-load (excluded from prompt token counting until called). |

### `[[tools.define]]` -- inline tool declarations

Each `[[tools.define]]` entry declares an inline tool. These are shape-checked at agent-load time; full parameter/execution/output validation is deferred to the `DataToolDefinition` schema at build time.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Tool name. Must be non-empty. Must be unique within the file. |
| `description` | string | yes | Tool description. Must be non-empty. |
| `namespace` | string | yes | Tool namespace. Must be non-empty. |
| `deferred` | boolean | yes | Whether to defer-load this tool. |
| `tags` | array of string | no | Classification tags. |
| `params` | table | no | Parameter definitions (opaque at agent-load; validated at build time). |
| `execution` | table | yes | Execution configuration (opaque at agent-load; validated at build time). |
| `output` | table | no | Output configuration (opaque at agent-load; validated at build time). |

### Example

```toml
format_version = 1

[agent]
name = "coder"
description = "Agent that writes code, runs tests, and commits changes."
prompt = "coder_agent.md"
category = "default"

[tools]
allow = ["read", "write", "edit", "git", "custom.*", "start_task", "end_task"]

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

## workflow.toml

Workflow definitions describe a DAG of tasks for the scheduler to execute. Load with `orxtra.scheduler.load_workflow(path_or_string)`.

### `[workflow]` section

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Workflow identifier. Must be non-empty. |
| `description` | string | yes | Human-readable description. Must be non-empty. |
| `escalation_policy` | string | no | How to handle task failures. Default: `"continue_independent"`. |

**`escalation_policy` values:**

| Value | Behavior |
|---|---|
| `continue_independent` | Continue executing tasks that don't depend on the failed task. |
| `halt` | Stop scheduling new tasks but let running tasks finish. |
| `abort_all` | Cancel all running and pending tasks. |

### `[[tasks]]` -- task definitions

Each `[[tasks]]` entry defines one task. Tasks support exactly one execution mode, determined by which fields are present.

#### Identity and common fields

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Task name. Must be non-empty. Unique within sibling tasks. |
| `depends_on` | array of string | no | Names of sibling tasks that must complete before this task starts. References are validated against declared task names. |
| `variables` | array of string | no | Variable names this task consumes or produces. |
| `category` | string | no | Override the agent's default model category for this task. |
| `budget` | number | no | Budget cap in USD for this task. Must be >= 0. |
| `write_paths` | array of string | no | Override allowed write paths for this task. |
| `output_schema` | string | no | JSON Schema reference for structured output validation. |
| `on_success` | string | no | Callback reference (`"module:callable"`) invoked on task success. |
| `pre_retry` | string | no | Callback reference (`"module:callable"`) invoked before each retry attempt. |

#### Execution modes

Exactly one of the following execution mode groups must be present. The modes are mutually exclusive.

**Agent mode** -- `agent` + `task_prompt` (co-present: both required together):

| Field | Type | Required | Description |
|---|---|---|---|
| `agent` | string | yes (agent mode) | Name of the agent to execute this task. |
| `task_prompt` | string | yes (agent mode) | The prompt/instructions for the agent. |
| `timeout` | integer | conditionally required | Timeout in seconds. Required when `agent` is present. Must be >= 1. |
| `context_refinement` | boolean | conditionally required | Whether to apply context refinement. Required when `agent` is present. |

**Callable mode** -- a Python callable reference:

| Field | Type | Required | Description |
|---|---|---|---|
| `callable` | string | yes (callable mode) | Python callable reference (`"module:function"`). |

**Subtasks mode** -- nested task tree:

| Field | Type | Required | Description |
|---|---|---|---|
| `subtasks` | array of task | yes (subtasks mode) | Nested child tasks (recursive structure). |

**Wait-for mode** -- event-driven waking:

| Field | Type | Required | Description |
|---|---|---|---|
| `wait_for` | string | yes (wait_for mode) | Event type to wait for before proceeding. |

**Decision-point mode** -- human/overseer decision gate:

| Field | Type | Required | Description |
|---|---|---|---|
| `decision_point` | boolean | yes (decision_point mode) | Marks this task as requiring a decision. |

#### Retry fields

These fields become conditionally required when `retry` is set to a non-zero value.

| Field | Type | Required | Description |
|---|---|---|---|
| `retry` | integer | no | Number of retry attempts. Default: 0. Must be >= 0. |
| `retry_resume` | boolean | conditionally required | Whether to resume the agent's context on retry (vs. clean restart). Required when `retry > 0`. |
| `retry_inject_failure` | boolean | conditionally required | Whether to inject failure context into the retry prompt. Required when `retry > 0`. |

#### For-each fields

These fields become conditionally required when `for_each` is present.

| Field | Type | Required | Description |
|---|---|---|---|
| `for_each` | string | no | Variable name to iterate over (fan-out). |
| `for_each_abort_on_failure` | boolean | conditionally required | Whether to abort remaining iterations on failure. Required when `for_each` is present. |
| `max_concurrency` | integer | conditionally required | Maximum parallel iterations. Required when `for_each` is present. Must be >= 1. |

#### Pre-checks and post-checks

Both `[tasks.prechecks]` and `[tasks.postchecks]` share the same structure. They define verification gates for task entry and exit.

| Field | Type | Required | Description |
|---|---|---|---|
| `scripts` | array of string | no | Python callable references (`"module:callable"`). Each callable receives a `CheckContext` and returns a `CheckResult`. |
| `agents` | array of table | no | Agent-based checks (read-only reviewer agents). |

Each agent check entry:

| Field | Type | Required | Description |
|---|---|---|---|
| `agent` | string | yes | Name of the reviewer agent. |
| `task` | string | yes | Review task prompt. |
| `block_threshold` | string | yes | Minimum severity that blocks. One of: `"critical"`, `"major"`, `"minor"`, `"nit"`. |
| `variables` | array of string | no | Variables to pass to the reviewer. |

### `[dependencies]` section (optional)

A top-level dependency map as an alternative to per-task `depends_on`. Keys are task names, values are arrays of dependency task names. The loader merges these into each task's `depends_on` field.

```toml
[dependencies]
test = ["implement"]
lint = ["implement"]
```

### `[[services]]` -- long-running process declarations (optional)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Service identifier. |
| `start_command` | string | yes | Shell command to start the service. |
| `stop_command` | string | yes | Shell command to stop the service. |
| `health_check_command` | string | no | Shell command to check service health. |
| `port` | integer | no | Port the service listens on. |
| `ready_timeout` | integer | no | Seconds to wait for the service to become ready. Default: 30. Must be >= 0. |

### Example

```toml
format_version = 1

[workflow]
name = "feature-pipeline"
description = "Implement a feature with tests and lint check."

[[tasks]]
name = "implement"
agent = "coder"
task_prompt = "Implement the feature described in the project spec."
timeout = 600
context_refinement = true

[[tasks]]
name = "test"
agent = "coder"
task_prompt = "Write tests for the implementation."
depends_on = ["implement"]
timeout = 600
context_refinement = true

[tasks.postchecks]
scripts = ["myproject.checks:pytest_passes"]

[[tasks]]
name = "lint"
agent = "coder"
task_prompt = "Fix any lint issues found by ruff."
depends_on = ["implement"]
timeout = 600
context_refinement = true

[tasks.postchecks]
scripts = ["myproject.checks:ruff_passes"]
```

## categories.toml

Maps category names to `"provider/model"` strings. Categories provide a layer of indirection for model routing -- agents reference a category name, and the categories file resolves it to a concrete provider and model.

Load with `orxtra.agent.load_categories(path)`. Resolve with `orxtra.agent.resolve_category(agent, categories)`.

### `[categories]` section

A map of category name to model string. Keys must match `^[A-Za-z0-9_.-]+$`. Values are `"provider/model"` strings and must be non-empty.

### Example

```toml
format_version = 1

[categories]
default = "anthropic/claude-sonnet-4-6"
reasoning = "anthropic/claude-opus-4-6"
fast = "openai/gpt-4o-mini"
```

## Run configuration (run_config.toml)

Run configuration files drive `start_run_from_file` in `orxtra.services`. They bundle all paths, database connection, provider credentials, budget, and autonomy policy for a single run.

All fields are validated by a strictspec gate at load time. Path strings are coerced to `Path` objects and budget strings to `Decimal` after validation.

| Field | Type | Required | Description |
|---|---|---|---|
| `workflow_path` | string | yes | Path to the workflow TOML file. Must be non-empty. |
| `agents_dir` | string | yes | Path to the directory containing agent TOML files. Must be non-empty. |
| `knowledge_dir` | string | yes | Path to the directory containing knowledge constraint files. Must be non-empty. |
| `categories_path` | string | yes | Path to the categories TOML file. Must be non-empty. |
| `read_root` | string | yes | Root path for file read operations (sandbox boundary). Must be non-empty. |
| `db_url` | string | yes | PostgreSQL connection URL (e.g. `"postgres://user:pass@host/db"`). Must be non-empty. |
| `provider_configs` | map of map | yes | Provider configuration. Outer keys are provider names, inner maps contain provider-specific settings. |
| `budget` | string | yes | Total run budget in USD (parsed as `Decimal`). Must be non-empty. |
| `autonomy_level` | string | yes | How much autonomy the Overseer has. Must be non-empty. |
| `budget_exhaustion_policy` | string | no | What happens when the budget runs out. Default: `"unlimited"`. |
| `secrets_env` | map | no | Maps secret names to environment variable names for `{{secret:NAME}}` substitution. |
| `tools_dir` | string | no | Path to the directory containing data-defined tool TOML files. Must be non-empty when present. |

### `provider_configs` format

Each provider entry is a map with a required `type` field and provider-specific settings:

```toml
[provider_configs.anthropic]
type = "anthropic"
api_key = "sk-ant-..."

[provider_configs.openai]
type = "openai"
api_key = "sk-..."
```

**Supported provider types and their fields:**

| Provider type | Fields | Default |
|---|---|---|
| `anthropic` | `api_key`, `base_url`, `api_version`, `max_tokens` | `base_url = "https://api.anthropic.com"`, `api_version = "2023-06-01"`, `max_tokens = 128000` |
| `openai` | `api_key`, `base_url` | `base_url = "https://api.openai.com/v1"` |
| `google` | `api_key`, `base_url` | `base_url = "https://generativelanguage.googleapis.com/v1beta"` |

If `api_key` is omitted from the provider config, the provider reads it from the corresponding environment variable (see Environment Variables below).

### `autonomy_level` values

| Value | Autonomous actions |
|---|---|
| `low` | `read_only` |
| `medium` | `read_only`, `retry`, `budget_reallocation`, `concurrency`, `task_assumption` |
| `high` | All of `medium` plus `scope_change`, `architecture_decision`, `understanding_assumption` |
| `max` | All actions |

### `budget_exhaustion_policy` values

| Value | Behavior |
|---|---|
| `block_new` | Block creation of new tasks. |
| `cancel_all` | Cancel all running tasks. |
| `timeout_grace` | Allow a grace period before cancelling. |
| `unlimited` | No budget enforcement (default). |

### Example

```toml
format_version = 1

workflow_path = "./workflows/pipeline.toml"
agents_dir = "./agents/"
knowledge_dir = "./knowledge/"
categories_path = "./categories.toml"
read_root = "/home/user/project"
db_url = "postgres://orxtra:password@localhost:5432/orxtra"
budget = "10.00"
autonomy_level = "medium"
budget_exhaustion_policy = "block_new"
tools_dir = "./tools/"

[provider_configs.anthropic]
type = "anthropic"
api_key = "sk-ant-..."

[secrets_env]
GITHUB_TOKEN = "GITHUB_TOKEN"
SLACK_WEBHOOK = "SLACK_WEBHOOK_URL"
```

## Knowledge files (knowledge.toml)

Knowledge files inject constraints into a run's constraint memory. Place them in the `knowledge_dir` referenced by the run configuration. Load with `orxtra.overseer.load_knowledge_files(directory)`.

### `[[constraints]]`

| Field | Type | Required | Description |
|---|---|---|---|
| `text` | string | yes | The constraint text. Must be non-empty. |
| `tier` | string | yes | Constraint tier (passed through to `write_constraint`). Must be non-empty. |
| `kind` | string | yes | Constraint kind (passed through to `write_constraint`). Must be non-empty. |

### Example

```toml
format_version = 1

[[constraints]]
text = "All database migrations must be backward-compatible."
tier = "hard"
kind = "architecture"

[[constraints]]
text = "Prefer composition over inheritance."
tier = "soft"
kind = "style"
```

## Data tool definitions (tool.toml)

Data-defined tools are standalone TOML files in the `tools_dir`. They define custom tools with typed parameters and one of three execution backends: HTTP, Monty (sandboxed Python), or command. Load with `orxtra.tool.load_tool_definitions(directory)`.

### `[tool]` section

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Tool name. Must be non-empty. |
| `description` | string | yes | Tool description. Must be non-empty. |
| `namespace` | string | yes | Must start with `custom.` (regex: `^custom\.`). |
| `deferred` | boolean | yes | Whether to defer-load this tool. |
| `tags` | array of string | no | Classification tags (e.g. `"readonly"`, `"mutation"`). |

### `[params]` section (optional)

A map of parameter name to parameter definition. Keys must match `^[A-Za-z_][A-Za-z0-9_]*$`.

Each parameter:

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | One of: `"string"`, `"integer"`, `"number"`, `"boolean"`. |
| `description` | string | yes | Parameter description. |
| `required` | boolean | yes | Whether the parameter is required. |
| `pattern` | string | no | Regex pattern for string validation. |

### `[execution]` section

A discriminated union on the `type` field. Exactly one execution type must be configured.

#### HTTP execution (`type = "http"`)

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | literal | yes | Must be `"http"`. |
| `method` | string | yes | HTTP method. One of: `"GET"`, `"HEAD"`, `"POST"`, `"PUT"`, `"DELETE"`, `"PATCH"`. |
| `url` | string | yes | URL template. Must be non-empty. Supports `{{param}}` substitution. |
| `headers` | map of string | no | HTTP headers. Supports `{{secret:NAME}}` substitution. |
| `body_template` | string | no | Request body template with `{{param}}` substitution. |

#### Monty execution (`type = "monty"`)

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | literal | yes | Must be `"monty"`. |
| `code` | string | yes | Python code to execute in the Monty sandbox. Must be non-empty. |
| `capabilities` | array of string | yes | Required sandbox capabilities. |
| `limits` | table | yes | Resource limits. |

Limits table:

| Field | Type | Required | Description |
|---|---|---|---|
| `max_duration_secs` | integer | yes | Maximum execution time in seconds. |
| `max_allocations` | integer | no | Maximum memory allocations. |
| `max_memory` | integer | no | Maximum memory usage in bytes. |

#### Command execution (`type = "command"`)

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | literal | yes | Must be `"command"`. |
| `executable` | string | yes | Executable name or path. Must be non-empty. |
| `arg_validation` | boolean | yes | Whether to validate arguments. |
| `timeout_ceiling` | integer | yes | Maximum execution time in seconds. |

### `[output]` section (optional)

| Field | Type | Required | Description |
|---|---|---|---|
| `schema` | table | yes | JSON Schema object for output validation (validated at execution time by jsonschema). |

## A2A skill definitions (skill.schema.toml)

A2A skill descriptors map A2A protocol skill IDs to orxtra capabilities.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | yes | A2A skill identifier. Must be non-empty. |
| `name` | string | yes | Human-readable skill name. Must be non-empty. |
| `description` | string | yes | Skill description. Must be non-empty. |
| `capability_name` | string | yes | Name of the orxtra capability this skill maps to. Must be non-empty. Validated at load time against registered capabilities. |
| `input_modes` | array of string | no | Accepted input MIME types. |
| `output_modes` | array of string | no | Produced output MIME types. |

## Environment variables

### LLM provider API keys

These are read by the transport providers when `api_key` is not explicitly passed in the provider configuration:

| Variable | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | `AnthropicProvider` (required if `api_key` not in provider config) |
| `OPENAI_API_KEY` | `OpenAIProvider` (required if `api_key` not in provider config) |
| `GOOGLE_API_KEY` | `GoogleProvider` (required if `api_key` not in provider config) |

Missing environment variables when the provider needs them produce a `KeyError`, not a silent fallback.

### Worker environment variables

These are set inside Docker worker containers by `DockerWorker`:

| Variable | Description |
|---|---|
| `ORXTRA_BRAIN_URL` | WebSocket URL for the brain connection. |
| `ORXTRA_API_KEY` | API key for worker authentication. |
| `ORXTRA_ROOT` | Project root path inside the container (always `/project`). |

### Secret substitution

Secret references in tool arguments (`{{secret:NAME}}`) are resolved by the secrets module. The `secrets_env` map in run configuration defines which environment variables back which secret names. The `create_secret_registry` factory reads each mapped environment variable from `os.environ` -- a missing variable is a hard error (`KeyError`), never a silent default.

## Database configuration

orxtra uses PostgreSQL (via asyncpg) for persistent state. The database URL is specified in the run configuration's `db_url` field as a standard PostgreSQL connection string:

```
postgres://user:password@host:port/database
```

Both userinfo passwords (`postgres://u:pw@host/db`) and query-parameter passwords (`postgres://host/db?password=pw`) are supported. Passwords are redacted in serialized run configs stored in the database.

Each module owns its own schema, managed by pgdesign via TOML schema files in the `schema/` directory:

| Schema file | Owner module | Tables |
|---|---|---|
| `schema/trace.toml` | trace | events, runs, tasks, transcripts, decisions, constraints |
| `schema/dispatch.toml` | dispatch | sources, subscriptions, subscription_actions, accumulator_buffer |
| `schema/identity.toml` | identity | principals |
| `schema/auth.toml` | auth | consumers, credentials |
| `schema/notification.toml` | notification | notification deliveries |
