# Tool Module Design

## Core Axiom

A tool is a single Python object that bundles schema and implementation together. No separation between "tool definition" (JSON schema somewhere) and "tool implementation" (method on a class somewhere else). One object, four fields.

## Tool Contract

The `Tool` dataclass is defined in `orxt.protocols._tool` (shared across transport, tool, and scheduler):

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the tool's arguments
    execute: Callable[[dict], Awaitable[str]]  # async function: args dict -> result string
```

## Tool Registry

- A `ToolRegistry` is a `dict[str, Tool]` -- literally a dictionary
- No singleton, no global state, no auto-discovery
- The caller constructs the registry and passes it to the scheduler

## Tool Filtering (Permission Enforcement)

When spawning an agent, the executor filters the registry to only include tools in the agent's `allow` list. The agent never sees tools that are not in its filtered set.

In `consult` mode (read-only agents), the following tools are mechanically stripped regardless of the `allow` list:
- File mutation: `write`, `edit`, `delete`, `move`, `copy` (destination), `mkdir`, `set_executable`
- Execution: `exec`
- Git mutations: `git` (mutation subcommands)
- HTTP mutations: `http` (POST, PUT, DELETE, PATCH methods stripped; GET and HEAD retained)
- Task lifecycle: `start_task`, `end_task`, `create_task`, `create_workflow`

## Active Task Enforcement

All tool calls require an active task. The executor checks with the scheduler that the calling agent has an active task. A tool call without an active task is a hard error.

Exception: `start_task` can be called without an active task -- it is how the agent enters one.

## Path Enforcement

Every scoped tool (file tools, git, exec) enforces path containment via a single canonical function:

1. Resolve the raw path against the boundary root
2. Canonicalize with `Path.resolve()` (symlink-aware)
3. Validate: `canonical == root` or `canonical` starts with `root + os.sep`
4. Escapes are hard errors at the tool layer (`ToolError`)

Two boundaries:
- **Read boundary** (`read_root`): the project root. Read tools cannot see files outside.
- **Write scope** (`write_scope`): per-task file paths. Write tools cannot mutate files outside. When `None`, writes are unrestricted within the read boundary.

The scheduler re-constructs write/edit tools per task when `write_paths` is declared.

## No-Truncation Design

orxt never discards tool output. Every tool result is persisted in full to the database.

For tools that can produce large output (read, exec, http, grep):
- Constructor takes `preview_threshold` (required): results under this size are returned in full
- Constructor takes `preview_lines` (required): how many head/tail lines the preview shows
- Large results return a preview with the note that `full=true` is available
- `full=true` only works if the session already received the preview for that path
- `make_read_tool` accepts an optional `previewer` callable for domain-specific output

## Write Safety

The scheduler instantiates write-safety infrastructure at run start and passes it to tool constructors. Tools enforce the mechanisms at execution time:

1. **Atomic replace**: temp file + fsync + rename
2. **Per-path write queue**: concurrent writes serialized
3. **Transient-only executor replay**: transient OS errors retried from recorded args
4. **Stale-write detection**: content hash tracking per agent session

## Built-in Tool Constructors

### File Read Tools

#### `make_read_tool(read_root, preview_threshold, preview_lines, previewer=None) -> Tool`

Read file contents with line numbers.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path relative to read_root |
| `offset` | integer | no | 1-based start line |
| `limit` | integer | no | Number of lines to read |
| `full` | boolean | no | Request full content after preview |

#### `make_list_dir_tool(read_root) -> Tool`

List directory contents with type and size information.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Directory path |
| `recursive` | boolean | no | Recurse into subdirectories (default false) |
| `pattern` | string | no | fnmatch filter |
| `max_results` | integer | no | Cap on entries (default 500) |

#### `make_glob_tool(read_root) -> Tool`

Find files by glob pattern. Results sorted by path (deterministic).

#### `make_grep_tool(read_root, preview_threshold, preview_lines) -> Tool`

Search file contents by regex.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | yes | Regex pattern |
| `path` | string | no | Search directory (default: read_root) |
| `case_sensitive` | boolean | no | Default true |
| `context_lines` | integer | no | Context around matches (default 0) |
| `max_results` | integer | no | Cap on matches (default 100) |
| `include` | string | no | fnmatch filter for filenames |
| `mode` | enum | no | `content` (default), `files_only`, `count` |

#### `make_stat_tool(read_root) -> Tool`

File metadata. Returns JSON: `{path, byte_size, line_count, language, mtime, binary, exists}`.

#### `make_diff_tool(read_root) -> Tool`

Unified diff between two files.

### File Write Tools

All enforce write scope, atomic replace, per-path serialization, stale-write detection, and transient replay.

#### `make_write_tool(read_root, write_scope=None) -> Tool`

Create or overwrite a file. Stale-write detection: hard error if the file exists and the agent has not read its current version.

#### `make_edit_tool(read_root, write_scope=None) -> Tool`

Find-and-replace. Requires exactly one match unless `replace_all=true`.

#### `make_mkdir_tool(read_root, write_scope=None) -> Tool`

Create a directory. Creates parents. No error if exists.

#### `make_move_tool(read_root, write_scope=None) -> Tool`

Move/rename. Both source and destination must be within write scope.

#### `make_copy_tool(read_root, write_scope=None) -> Tool`

Copy a file. Source uses read boundary, destination uses write scope.

#### `make_delete_tool(read_root, write_scope=None) -> Tool`

Delete a file or directory. **Wraps saferm.** Every deletion has a mandatory `description` for the audit trail.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Path to delete |
| `description` | string | yes | Why this deletion is needed |
| `recursive` | boolean | yes | Required for directories |

#### `make_set_executable_tool(read_root, write_scope=None) -> Tool`

Set executable bit (chmod +x).

### Git Tool

#### `make_git_tool(read_root, allowed_subcommands) -> Tool`

Git operations with subcommand-level granularity. **Mutation subcommands wrap safegit.**

**Read-only tier** (raw git): `status`, `diff`, `log`, `show`, `blame`, `branches`, `changed_files`

**Mutation tier** (safegit): `commit`

The `commit` subcommand takes `message` (required) and `files` (required, explicit list). Wraps `safegit commit` with run/task/agent context trailers.

`push`, `pull`, `reset`, `checkout`, `stash`, `restore` are absent. Pushes happen through rlsbl.

### Execution Tool

#### `make_exec_tool(executable, description, arg_schema, read_root, timeout_ceiling) -> Tool`

Bind one fixed executable with typed arguments. The agent cannot control which binary runs.

Returns: `{stdout, stderr, exit_code, timed_out, duration_ms}`.

### HTTP Tool

#### `make_http_tool(allowed_hosts, timeout_ceiling=30) -> Tool`

HTTP requests with host-level access control.

In consult mode, POST/PUT/DELETE/PATCH are stripped. Only GET and HEAD are available.

### Delegation Tools

#### `make_consult_tool(executor) -> Tool`

Creates a read-only agent session for research.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `agent` | string | yes | Agent name |
| `question` | string | yes | The question or research task |
| `variables` | dict | no | Variables for prompt substitution |

Returns: `str` -- the agent's text response. The spawned agent has write/edit/delete/move/mkdir/set_executable/exec/git-mutations stripped. HTTP has mutating methods stripped. Task lifecycle tools stripped.

#### `make_notepad_tool(trace_writer) -> Tool`

Writes an entry to the run's shared notepad.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `type` | enum | yes | `learning`, `decision`, `issue` |
| `text` | string | yes | One fact/decision/issue per entry |

### Task Lifecycle Tools

#### `make_start_task_tool(scheduler_ref) -> Tool`

Enter a task. The scheduler runs the task's pre-checks. If all pass, the task becomes active and tool calls are permitted. If any fail, returns the check results as an error.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | yes | The task to enter (injected into agent prompt) |

Returns: success or pre-check failure details.

Can be called without an active task (it is how the agent enters one).

#### `make_end_task_tool(scheduler_ref) -> Tool`

Complete the active task. The `message` parameter serves two purposes: it is always recorded as the task summary in the trace, and if the task produced file changes (any file-mutating tool was called during the task), it is also used as the commit message via safegit. If file-mutating tools were called and uncommitted changes exist, the executor commits automatically before running post-checks. Post-checks verify the committed state.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `message` | string | yes | What the agent accomplished. Always required. Used as task summary and commit message. |

Returns: success or post-check failure details.

#### `make_create_task_tool(scheduler_ref) -> Tool`

Create a concrete subtask within the current active task. The scheduler validates the specification, creates the subtask, and spawns the specified agent.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Task name |
| `agent` | string | yes | Agent definition name |
| `task_prompt` | string | yes | Task prompt template |
| `prechecks` | array | no | Pre-check Execution specs |
| `postchecks` | array | no | Post-check Execution specs |
| `variables` | object | no | Variables for prompt substitution |
| `timeout` | integer | yes | Max wall-clock seconds |
| `context_refinement` | boolean | yes | Whether the Overseer refines context |
| `category` | string | no | Override agent's default category |
| `budget` | number | no | Per-task USD budget |
| `write_paths` | array | no | File paths this task may write |
| `retry` | integer | no | Max retry count (default 0) |
| `retry_resume` | boolean | conditional | Required if retry > 0 |
| `retry_inject_failure` | boolean | conditional | Required if retry > 0 |
| `depends_on` | array of strings | no | Sibling task names that must complete first |

Returns: `task_id` or validation error.

#### `make_create_workflow_tool(scheduler_ref) -> Tool`

Create a goal-oriented task tree within the current active task. A workflow agent decomposes it into subtasks.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Workflow name |
| `description` | string | yes | What this workflow accomplishes |
| `goals` | array of strings | yes | Goal descriptions |
| `postchecks` | array | no | Post-check Execution specs |
| `budget` | number | no | USD budget |

Returns: `workflow_id` or validation error.

## Tool Execution

When the LLM requests a tool call, the transport's tool-call loop:

1. Checks that the agent has an active task (hard error if not, except for `start_task`)
2. Looks up the tool in the filtered registry by name
3. Validates arguments against `parameters` (JSON Schema)
4. Records tool call start time
5. For write tools: acquires per-path write lock, checks stale-write detection
6. For secret-bearing arguments: substitutes `{{secret:NAME}}` placeholders
7. Calls `execute(args)` and awaits the result
8. Scrubs registered secret values from the result
9. Records duration_ms in the ToolUse trace event
10. Returns the result (applying preview if over threshold)

## Files

| File | Contents |
|---|---|
| `_types.py` | Re-exports `Tool` and `ToolError` from `orxt.protocols._tool`. |
| `_path.py` | Canonical path enforcement. |
| `_preview.py` | No-truncation preview logic. |
| `_write_queue.py` | Per-path write serialization, stale-write detection, transient replay. |
| `_constructors.py` | File read/write tool constructors: `make_read_tool`, `make_write_tool`, `make_edit_tool`, `make_list_dir_tool`, `make_glob_tool`, `make_grep_tool`, `make_stat_tool`, `make_diff_tool`, `make_mkdir_tool`, `make_move_tool`, `make_copy_tool`, `make_delete_tool`, `make_set_executable_tool`, `make_git_tool`, `make_exec_tool`, `make_http_tool`. |
| `_consult.py` | `make_consult_tool(executor)` -- consult tool with read-only stripping. |
| `_notepad.py` | `make_notepad_tool(trace_writer)` -- notepad tool. |
| `_task_tools.py` | `make_start_task_tool`, `make_end_task_tool`, `make_create_task_tool`, `make_create_workflow_tool`. Task lifecycle tools. |
| `_validation.py` | JSON Schema argument validation. |

## What This Module Does NOT Do

- Does not manage tool permissions (that is the agent module and scheduler)
- Does not execute tools autonomously (that is the scheduler)
- Does not define mandatory tools or require specific tools to exist
- Does not have a plugin system for tool discovery
- Does not ship a bash/shell tool
- Does not hide LLM calls inside any tool implementation (except consult, which is explicit delegation)
