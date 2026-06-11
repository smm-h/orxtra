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
- Framework tools (e.g., `notepad`) are injected by the executor outside the whitelist
- The filtered registry is what gets sent to the LLM as available tools
- The agent never sees tools that aren't in its filtered set
- This is the mechanical enforcement of permissions -- no prompt instructions needed

## Built-in Tools

oxtra ships zero built-in tools. The consuming project defines its own tools. However, oxtra provides **tool constructors** for common patterns:

| Constructor | Purpose |
|---|---|
| `make_read_tool(cwd: Path) -> Tool` | Read files relative to cwd |
| `make_write_tool(cwd: Path) -> Tool` | Write files relative to cwd |
| `make_edit_tool(cwd: Path) -> Tool` | Edit files (find/replace) |
| `make_bash_tool(cwd: Path, timeout: int) -> Tool` | Run shell commands with timeout |
| `make_grep_tool(cwd: Path) -> Tool` | Search file contents |
| `make_glob_tool(cwd: Path) -> Tool` | Find files by pattern |

These are convenience functions, not special. They return plain `Tool` objects. Users can replace them or not use them.

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
