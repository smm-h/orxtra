# Tool Module Design

## Core Axiom

A tool is a single Python object that bundles schema and implementation together. No separation between "tool definition" (JSON schema somewhere) and "tool implementation" (method on a class somewhere else). One object, four fields.

## Tool Contract

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the tool's arguments
    execute: Callable[[dict], Awaitable[str]]  # async function: args dict -> result string
```

That's the entire contract. No `ToolDefinition` vs `ToolImpl`. No `register()` ceremony. No lifecycle hooks. No side-channel state.

## Tool Registry

- A `ToolRegistry` is a `dict[str, Tool]` -- literally a dictionary
- No singleton, no global state, no auto-discovery
- The caller constructs the registry and passes it to the scheduler
- Adding a tool: `registry["my_tool"] = Tool(name="my_tool", ...)`
- Removing a tool: `del registry["my_tool"]`

## Tool Filtering (Permission Enforcement)

- When spawning an agent, the executor filters the registry to only include tools in the agent's `allow` list
- `spawn` is mechanically stripped from all spawned agents regardless of `allow`
- The filtered registry is what gets sent to the LLM as available tools
- The agent never sees tools that aren't in its filtered set
- This is the mechanical enforcement of permissions -- no prompt instructions needed

## Path Enforcement

Every scoped tool (file tools, git, exec) enforces path containment via a single canonical function:

1. Resolve the raw path against the boundary root
2. Canonicalize with `Path.resolve()` (symlink-aware)
3. Validate: `canonical == root` or `canonical` starts with `root + os.sep`
4. Escapes are hard errors at the tool layer (`ToolError`)

Two boundaries, configured at construction:

- **Read boundary** (`read_root`): the project root. Read tools (read, list_dir, grep, glob, stat, diff) cannot see files outside this root.
- **Write scope** (`write_scope`): per-step file paths. Write tools (write, edit, delete, move, copy destination, mkdir, set_executable) cannot mutate files outside this scope. When `None`, writes are unrestricted within the read boundary.

The scheduler re-constructs write/edit tools per step when `write_paths` is declared in the workflow TOML. Steps without `write_paths` get unrestricted tools (scope=None). Out-of-scope writes trigger the Overseer's `scope_decision` protocol.

## No-Truncation Design

oxtra never discards tool output. Every tool result is persisted in full to the database.

For tools that can produce large output (read, exec, http, grep):

- Constructor takes `preview_threshold` (required): results under this size are returned in full
- Constructor takes `preview_lines` (required): how many head/tail lines the preview shows
- Large results return a preview: line/byte count, first N lines, last N lines, and the note that `full=true` is available
- `full=true` only works if the session already received the preview for that path -- the executor enforces this
- `make_read_tool` accepts an optional `previewer` callable replacing the default head/tail preview with domain-specific output

## Write Safety

The executor enforces four write-safety mechanisms on all file-mutating tools:

1. **Atomic replace**: temp file + fsync + rename in the same directory; a crash never leaves a torn file
2. **Per-path write queue**: concurrent writes to the same path are serialized; edit's read-modify-replace is race-free
3. **Transient-only executor replay**: if a write fails for transient OS reasons (EIO, EBUSY), the executor retries from the recorded tool arguments -- the agent is never asked to re-emit content. Deterministic errors (scope violation, hunk mismatch, stale-write) always surface to the agent.
4. **Stale-write detection**: the executor tracks the content hash of each file at each agent's last read. Write/edit on an existing file hard-errors if (a) the agent has never read its current version, or (b) the file changed since the agent's last read. New file creation needs no prior read.

## Built-in Tool Constructors

oxtra provides tool constructors -- functions that return `Tool` objects. The consumer calls them and adds results to the registry. There is no distinction between 'framework tools' and 'domain tools' -- all tools are the same type.

No bash tool. A consumer who truly needs raw shell writes their own `Tool` in ten lines -- oxtra refuses to bless one.

### Enforced Discipline via Tool Design

Agents are prone to shortcuts. The tool constructors enforce disciplined workflows by construction, not by instruction:

- **Git mutations wrap safegit.** `commit` uses `safegit commit` which is concurrency-safe and requires explicit file lists. No `git add .`, no `git stash`, no destructive resets -- these operations don't exist in the tool's parameter schema.
- **File deletion wraps saferm.** Every deletion requires a mandatory `description` explaining why. Deletions have an audit trail and are recoverable. No raw `rm`.
- **No push.** Absent from the git tool entirely. Pushes happen through rlsbl release flows, not through agent tools.
- **No bash escape hatch.** Since there's no shell tool, agents cannot bypass safegit/saferm by running raw commands. The disciplined tools are the only path.

This embodies the "agent experience over agent convenience" principle: make the agent's life harder when it produces more correct outcomes. The tool constructors are the mechanical enforcement of conventions that would otherwise be ignored.

### File Read Tools

#### `make_read_tool(read_root, preview_threshold, preview_lines, previewer=None) -> Tool`

Read file contents with line numbers.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path relative to read_root |
| `offset` | integer | no | 1-based start line |
| `limit` | integer | no | Number of lines to read |
| `full` | boolean | no | Request full content after receiving a preview. Only honored if a preview for this path was already returned in the current session. |

Returns file content with line numbers (cat -n format). Binary files are detected and rejected.

#### `make_list_dir_tool(read_root) -> Tool`

List directory contents with type and size information.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Directory path relative to read_root |
| `recursive` | boolean | no | Recurse into subdirectories (default false) |
| `pattern` | string | no | fnmatch filter pattern |
| `max_results` | integer | no | Cap on returned entries (default 500) |

Returns tab-separated entries: `{file|dir|link}\t{size}\t{path}`. Respects .gitignore patterns.

#### `make_glob_tool(read_root) -> Tool`

Find files by glob pattern.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | yes | Glob pattern |
| `path` | string | no | Base directory (default: read_root) |
| `max_results` | integer | no | Cap on returned entries (default 200) |

Returns relative paths sorted by path (not mtime -- deterministic across runs). Respects .gitignore.

#### `make_grep_tool(read_root, preview_threshold, preview_lines) -> Tool`

Search file contents.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `pattern` | string | yes | Regex pattern |
| `path` | string | no | Search directory (default: read_root) |
| `case_sensitive` | boolean | no | Default true |
| `context_lines` | integer | no | Lines of context around matches (default 0) |
| `max_results` | integer | no | Cap on returned matches (default 100) |
| `include` | string | no | fnmatch filter for filenames |
| `mode` | enum | no | `content` (default), `files_only`, `count` |

Returns matching lines with file path and line number. Subject to the no-truncation preview mechanism for large result sets.

#### `make_stat_tool(read_root) -> Tool`

File metadata.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path (supports glob patterns) |

Returns JSON: `{path, byte_size, line_count, language, mtime, binary, exists}`. Glob patterns return an array.

#### `make_diff_tool(read_root) -> Tool`

Unified diff between two files.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path_a` | string | yes | First file path |
| `path_b` | string | yes | Second file path |

Returns unified diff format.

### File Write Tools

All write tools enforce write scope, atomic replace, per-path serialization, stale-write detection, and transient replay.

#### `make_write_tool(read_root, write_scope=None) -> Tool`

Create or overwrite a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path relative to read_root |
| `content` | string | yes | File content |
| `create_dirs` | boolean | no | Create parent directories (default false) |

Stale-write detection: hard error if the file exists and the agent has not read its current version.

#### `make_edit_tool(read_root, write_scope=None) -> Tool`

Find-and-replace in a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path |
| `old_string` | string | yes | Text to find |
| `new_string` | string | yes | Replacement text |
| `replace_all` | boolean | no | Replace all occurrences (default false) |

Requires exactly one match unless `replace_all=true`. Zero matches or multiple matches (without `replace_all`) are hard errors. Subject to stale-write detection.

#### `make_mkdir_tool(read_root, write_scope=None) -> Tool`

Create a directory.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Directory path |

Creates parents (`mkdir -p` behavior). No error if exists.

#### `make_move_tool(read_root, write_scope=None) -> Tool`

Move/rename a file or directory.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `source` | string | yes | Source path |
| `destination` | string | yes | Destination path |

Both source and destination must be within write scope.

#### `make_copy_tool(read_root, write_scope=None) -> Tool`

Copy a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `source` | string | yes | Source path (read boundary -- can copy from anywhere in the project) |
| `destination` | string | yes | Destination path (write scope) |

Asymmetric: source uses read boundary, destination uses write scope. Preserves file metadata.

#### `make_delete_tool(read_root, write_scope=None) -> Tool`

Delete a file or directory. **Wraps saferm, not rm.** Every deletion has an audit trail and is recoverable via `saferm undelete`. Agents cannot bypass this -- there is no raw `rm` tool.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Path to delete |
| `description` | string | yes | Why this deletion is needed. Mandatory -- saferm requires it for the audit trail. |
| `recursive` | boolean | yes | Required for directories. Hard error if deleting a directory without `recursive=true`. |

#### `make_set_executable_tool(read_root, write_scope=None) -> Tool`

Set the executable bit on a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | File path |

Sets user/group/other executable bits (chmod +x).

### Git Tool

#### `make_git_tool(read_root, allowed_subcommands) -> Tool`

Git operations with subcommand-level granularity. **Mutation subcommands wrap safegit, not raw git.** `commit` uses `safegit commit -m "message" -- file1 file2` which is concurrency-safe and handles both tracked and untracked files. Agents cannot use raw git -- there is no bash tool and no raw git tool.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `subcommand` | string | yes | Git subcommand (must be in the allowed list) |
| `args` | array of strings | no | Arguments to the subcommand |

`allowed_subcommands` is a required constructor parameter -- no default. The consumer picks from the menu:

**Read-only tier** (raw git): `status`, `diff`, `log`, `show`, `blame`, `branches`, `changed_files`

**Mutation tier** (safegit): `commit`

The `commit` subcommand:
- Takes `message` (required) and `files` (required, explicit list -- no `git add .` or `git add -A`)
- Wraps `safegit commit -m "message" -- file1 file2`
- Concurrency-safe: multiple agents can commit simultaneously without corruption
- Hard error if `files` is empty or if `message` is empty

`stage` is removed -- safegit handles staging internally within the commit operation. `push`, `pull`, `reset`, `checkout`, `stash`, `restore` are deliberately absent. Pushes happen through rlsbl, not through agent tools.

Working directory is the read_root.

### Execution Tool

#### `make_exec_tool(executable, description, arg_schema, read_root, timeout_ceiling) -> Tool`

Bind one fixed executable with typed arguments.

| Constructor param | Type | Required | Description |
|---|---|---|---|
| `executable` | string | yes | The fixed executable name (e.g., "pytest", "uv", "npm"). The agent cannot control which binary runs. |
| `description` | string | yes | Tool description for the LLM |
| `arg_schema` | dict | yes | JSON Schema for the arguments the agent can pass |
| `read_root` | Path | yes | Working directory |
| `timeout_ceiling` | integer | yes | Maximum allowed timeout in seconds |

The tool's runtime parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `args` | array of strings | no | Arguments to pass to the executable |
| `timeout` | integer | no | Per-call timeout (capped at `timeout_ceiling`) |

**Timeout discipline**: default timeout = `timeout_ceiling`. On timeout: SIGTERM → 5s grace wait → SIGKILL. The `timed_out` flag is set in the result.

**Structured result**: `{stdout, stderr, exit_code, timed_out, duration_ms}`. Non-zero exit codes are data, not exceptions. Subject to the no-truncation preview mechanism for large stdout/stderr.

### HTTP Tool

#### `make_http_tool(allowed_hosts, timeout_ceiling=30) -> Tool`

HTTP requests with host-level access control.

| Constructor param | Type | Required | Description |
|---|---|---|---|
| `allowed_hosts` | list of strings or `"allow_all"` | yes | Hostnames the tool can reach. Required -- no default. |
| `timeout_ceiling` | integer | no | Maximum allowed timeout in seconds (default 30) |

| Parameter | Type | Required | Description |
|---|---|---|---|
| `method` | enum | yes | GET, POST, PUT, DELETE, PATCH, HEAD |
| `url` | string | yes | Target URL (hostname must be in `allowed_hosts`) |
| `headers` | object | no | Request headers |
| `body` | string | no | Request body |
| `timeout` | integer | no | Per-request timeout (capped at `timeout_ceiling`) |

Returns JSON: `{status_code, headers, body, elapsed_ms}`. Response body subject to the no-truncation preview mechanism.

### Agent Tools

#### `make_spawn_tool(executor) -> Tool`

Creates a full agent session with write access.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `agent` | string | yes | Agent name (must exist in loaded agents) |
| `task` | string | yes | Task prompt for the agent |
| `category` | string | no | Override the agent's default category |
| `variables` | dict | no | Variables to substitute into the agent's prompt template |
| `run_in_background` | boolean | yes | True = async (returns session_id immediately), false = sync. No default -- must be explicit. |

Returns: `{session_id, output}`.

`spawn` is mechanically stripped from all spawned agents' tool sets. Only the scheduler retains access.

#### `make_consult_tool(executor) -> Tool`

Creates a read-only agent session for research.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `agent` | string | yes | Agent name |
| `question` | string | yes | The question or research task |
| `variables` | dict | no | Variables to substitute into the agent's prompt template |

Returns: `str` -- the agent's text response.

The spawned agent has write, edit, delete, move, mkdir, set_executable, spawn, git (mutation subcommands), and exec mechanically removed from its tool set.

#### `make_notepad_tool(trace_writer) -> Tool`

Writes an entry to the run's shared notepad.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `type` | enum | yes | `learning`, `decision`, `issue` |
| `text` | string | yes | Free-form content. One fact/decision/issue per entry. |

The `step` and `agent` fields in the entry are injected by the executor.

## Tool Execution

When the LLM requests a tool call, the transport's tool-call loop:

1. Looks up the tool in the filtered registry by name
2. Validates the arguments against `parameters` (JSON Schema validation)
3. Records the tool call start time
4. For write tools: acquires the per-path write lock, checks stale-write detection
5. For secret-bearing arguments: substitutes `{{secret:NAME}}` placeholders with real values
6. Calls `execute(args)` and awaits the result
7. For the result: scrubs any registered secret values, replacing them with their placeholders
8. Records duration_ms in the ToolUse trace event
9. Returns the result string to the LLM (applying preview if over threshold)

Error handling:

- Tool name not in registry: hard error, not a graceful fallback
- Argument validation fails: hard error with details sent back to the LLM
- Stale-write detection fails: hard error ("file changed since you read it")
- Path escapes boundary: hard error
- `execute()` raises: the exception message is sent back to the LLM as an error result
- Transient write failure (EIO, EBUSY): executor replays from recorded args (invisible to agent)

## Files

| File | Contents |
|---|---|
| `_types.py` | `Tool` frozen dataclass: name, description, parameters (JSON Schema dict), execute (async callable). `ToolError` exception. |
| `_path.py` | Canonical path enforcement: `resolve_and_check(raw_path, root)`. Used by every scoped tool. |
| `_preview.py` | No-truncation preview logic: check threshold, build head/tail preview, enforce escalation guard (full=true only after preview). |
| `_write_queue.py` | Per-path write serialization, stale-write detection (content hash tracking per session), transient-only replay logic. |
| `_constructors.py` | `make_read_tool`, `make_write_tool`, `make_edit_tool`, `make_list_dir_tool`, `make_glob_tool`, `make_grep_tool`, `make_stat_tool`, `make_diff_tool`, `make_mkdir_tool`, `make_move_tool`, `make_copy_tool`, `make_delete_tool`, `make_set_executable_tool`, `make_git_tool`, `make_exec_tool`, `make_http_tool`. Each returns a `Tool`. |
| `_spawn.py` | `make_spawn_tool(executor)` -- returns the spawn `Tool`. Contains the spawn execution logic. |
| `_consult.py` | `make_consult_tool(executor)` -- returns the consult `Tool`. Contains the consult execution logic and tool stripping. |
| `_notepad.py` | `make_notepad_tool(trace_writer)` -- returns the notepad `Tool`. Delegates writes to the trace module. |
| `_validation.py` | JSON Schema argument validation used by the tool-call loop. |

## What This Module Does NOT Do

- Does not manage tool permissions (that's agent/ and scheduler/)
- Does not execute tools autonomously (that's scheduler/)
- Does not define mandatory tools or require specific tools to exist
- Does not have a plugin system for tool discovery
- Does not ship a bash/shell tool
- Does not hide LLM calls inside any tool implementation
