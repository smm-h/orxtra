# Tool Module Design

## Core Axiom

A tool is a single Python object that bundles schema and implementation together. No separation between "tool definition" (JSON schema somewhere) and "tool implementation" (method on a class somewhere else). One object, three fields.

## Tool Contract

```python
@dataclass
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
- The caller constructs the registry and passes it to the pipeline executor
- Adding a tool: `registry["my_tool"] = Tool(name="my_tool", ...)`
- Removing a tool: `del registry["my_tool"]`

## Tool Filtering (Permission Enforcement)

- When spawning an agent, the executor filters the registry to only include tools in the agent's `allow` list
- `spawn` is mechanically stripped from all spawned agents regardless of `allow`
- The filtered registry is what gets sent to the LLM as available tools
- The agent never sees tools that aren't in its filtered set
- This is the mechanical enforcement of permissions -- no prompt instructions needed

## Built-in Tools

oxtra provides **tool constructors** -- functions that return `Tool` objects. The consumer calls them and adds results to the registry. There is no distinction between 'framework tools' and 'domain tools' -- all tools are the same type.

| Constructor | Purpose |
|---|---|
| `make_read_tool(cwd: Path) -> Tool` | Read files relative to cwd |
| `make_write_tool(cwd: Path) -> Tool` | Write files relative to cwd |
| `make_edit_tool(cwd: Path) -> Tool` | Edit files (find/replace) |
| `make_bash_tool(cwd: Path, timeout: int) -> Tool` | Run shell commands with timeout |
| `make_grep_tool(cwd: Path) -> Tool` | Search file contents |
| `make_glob_tool(cwd: Path) -> Tool` | Find files by pattern |
| `make_spawn_tool(executor) -> Tool` | Spawn a full agent session |
| `make_consult_tool(executor) -> Tool` | Spawn a read-only agent session |
| `make_notepad_tool(run_dir: Path) -> Tool` | Write to the pipeline's shared notepad |

These are convenience functions, not special. They return plain `Tool` objects. Users can replace them or not use them.

## Spawn Tool

Creates a full agent session with write access. The pipeline executor uses this tool internally -- it is the single code path for all agent invocation.

Parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `agent` | string | yes | Agent name (must exist in loaded agents) |
| `task` | string | yes | Task prompt for the agent |
| `category` | string | no | Override the agent's default category for this invocation |
| `variables` | dict | no | Variables to substitute into the agent's prompt template |
| `run_in_background` | boolean | yes | True = async (returns immediately with session_id), false = sync (blocks until completion). No default -- must be explicit. |

Returns: `{session_id: str, output: str}` -- the session ID for resumption and the agent's text output.

`spawn` is mechanically stripped from all spawned agents' tool sets, regardless of their `allow` list. Only the pipeline executor and orchestrator-level agents retain access. This prevents orchestration recursion.

## Consult Tool

Creates a read-only agent session for research. The spawned agent has write, edit, bash, and spawn mechanically removed from its tool set.

Parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `agent` | string | yes | Agent name (must exist in loaded agents) |
| `question` | string | yes | The question or research task |
| `variables` | dict | no | Variables to substitute into the agent's prompt template |

Returns: `str` -- the agent's text response.

Any agent with `"consult"` in its `allow` list can use this tool. This is the mechanism for two-tier delegation: workers cannot spawn other workers, but they can consult read-only agents for information.

## Notepad Tool

Writes an entry to the pipeline's shared notepad for cross-agent context sharing. See `notepad/DESIGN.md` for the IPC mechanism, file format, and injection behavior.

Parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `type` | enum: "learning", "decision", "issue" | yes | Category of the entry |
| `text` | string | yes | Free-form content. One fact/decision/issue per entry. |

The `step` and `agent` fields in the JSONL entry are injected by the executor -- the agent only provides `type` and `text`.

Any agent with `"notepad"` in its `allow` list can use this tool. Agents without it in their `allow` list cannot write to the notepad.

## Tool Execution

When the LLM requests a tool call, the pipeline executor:

1. Looks up the tool in the filtered registry by name
2. Validates the arguments against `parameters` (JSON Schema validation)
3. Calls `execute(args)` and awaits the result
4. Returns the result string to the LLM

Error handling:

- Tool name not in registry: hard error, not a graceful fallback
- Argument validation fails: hard error with details sent back to the LLM
- `execute()` raises: the exception message is sent back to the LLM as an error result

## What This Module Does NOT Do

- Does not manage tool permissions (that's agent/ and pipeline/)
- Does not execute tools autonomously (that's pipeline/)
- Does not define mandatory tools or require specific tools to exist
- Does not have a plugin system for tool discovery
