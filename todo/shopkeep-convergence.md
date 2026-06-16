# Three Features: shell tool, tool.compose, orchestrator await_task

Filed by the e-commerce pipeline project. These three features address gaps discovered while integrating with the target project's connector generation and crawl orchestration workflows.

---

## Feature 1: Capability-scoped shell tool (`make_shell_tool`)

### Problem

`make_exec_tool` binds a single fixed executable with typed arguments -- one tool per binary. When an agent needs access to a curated set of CLI tools (e.g., `uv`, `pytest`, `ruff`, `node`, `npx`), the consumer must create N separate `make_exec_tool` instances. This clutters the tool list, wastes context window, and forces the agent to remember which tool name maps to which executable.

A full unrestricted bash tool is intentionally absent (per `tool/DESIGN.md` line 333: "Does not ship a bash/shell tool"). The gap is a middle ground: a single tool that accepts a command string, parses it, validates the binary against a whitelist, and executes via `subprocess_exec` (not `subprocess_shell`).

### Solution

A new `make_shell_tool` constructor in a new file `tool/src/orxt/tool/_shell_tool.py`.

### Affected files

| File | Action | Notes |
|------|--------|-------|
| `tool/src/orxt/tool/_shell_tool.py` | Create | New tool constructor |
| `tool/src/orxt/tool/__init__.py` (lines 1-85) | Edit | Add import and `__all__` entry |
| `tool/DESIGN.md` (after line 188) | Edit | Document the new tool |
| `tool/tests/test_shell_tool.py` | Create | Tests |
| `protocols/src/orxt/protocols/_tool.py` | No change | Reuses existing `Tool` dataclass |
| `tool/src/orxt/tool/_pipeline.py` (line 15-17) | Possibly edit | If shell is considered a file-mutation tool for mutation tracking |

### Design decisions

**Command parsing approach: `shlex.split`.**
- `shlex.split` handles quoting (`"foo bar"` stays as one token) and is the standard Python approach.
- After splitting, extract token[0] as the binary name, validate against the whitelist.
- No pipe support. Pipes imply `shell=True` which defeats the security model. If the agent needs pipes, it should use two tool calls and pass data through files. This is a hard constraint, not a soft one -- reject commands containing `|`, `&&`, `||`, `;`, backticks, `$()`, and `>` / `<` redirects at the parsing stage before execution.

**Binary validation: simple set membership.**
- Constructor takes `allowed_binaries: list[str]` (not `frozenset` -- the constructor freezes it).
- After `shlex.split`, check `tokens[0] in allowed_set`. Hard error if not.
- No path resolution -- the binary name is checked as-is (so `allowed_binaries=["uv"]` allows `uv` but not `/usr/bin/uv`). This keeps it simple and avoids TOCTOU path resolution issues.

**Parameters the constructor should accept:**

```python
def make_shell_tool(
    allowed_binaries: list[str],     # Whitelist of executable names
    description: str,                 # Tool description
    read_root: Path,                  # Working directory for subprocess
    timeout_ceiling: int,             # Max timeout in seconds
    preview_threshold: int,           # Byte threshold for output preview
    preview_lines: int,               # Head/tail lines in preview
    env_filter: dict[str, str] | None = None,  # Environment variable overrides
) -> Tool:
```

**JSON Schema for the tool:**

```python
{
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Shell command to execute. Only whitelisted binaries are allowed."
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "description": "Timeout in seconds. Capped at the configured ceiling."
        }
    },
    "required": ["command"],
    "additionalProperties": False
}
```

**Reusable infrastructure from `make_exec_tool`:**
- `check_and_preview` for output truncation (from `_preview.py`)
- `validate_args` for JSON Schema validation (from `_validation.py`)
- The `_SIGTERM_GRACE_SECONDS` timeout-and-kill pattern (lines 28, 83-98 of `_exec_tool.py`)
- The JSON result format: `{stdout, stderr, exit_code, timed_out, duration_ms}`

**Integration with SecretRegistry:**
- The pipeline wrapper (`_pipeline.py` lines 39-43) already handles secret substitution on the `args` dict before `execute()` is called. Since the command string is inside `args["command"]`, `{{secret:NAME}}` placeholders in the command will be substituted automatically.
- The pipeline wrapper (lines 53-55) also scrubs secrets from the result string after execution.
- No additional work needed in `make_shell_tool` itself.

**Integration with write_scope:**
- The shell tool is fundamentally different from file tools. File tools know the exact path being written. The shell tool runs an opaque command that could write anywhere.
- Two options: (a) treat the shell tool as not write-scoped and rely on the process's working directory being `read_root`, or (b) don't track individual file mutations but add the tool name to `FILE_MUTATION_TOOLS` if write-capable commands are in the whitelist.
- Recommendation: add `"shell"` to `FILE_MUTATION_TOOLS` (line 15-17 of `_pipeline.py`). The mutation tracker only needs a boolean "did this session mutate files?" -- it doesn't need to know which files. The auto-commit on `end_task` already uses `git status --porcelain` to find actual changed files.

**Shell metacharacter rejection:**
- Before `shlex.split`, scan the raw command for dangerous metacharacters: `|`, `&&`, `||`, `;`, `` ` ``, `$(`, `>`, `<`, `>>`, `<<`.
- Use a regex check, not string containment, to avoid false positives in quoted strings. Actually -- `shlex.split` does not evaluate metacharacters, it just tokenizes. The danger is only if `subprocess_exec` is replaced with `subprocess_shell`. Since we use `subprocess_exec`, metacharacters in arguments are harmless (they're literal strings). The real risk is the binary name.
- Simpler approach: just validate that `tokens[0]` is in the allowed set. The rest are literal arguments to that binary. No metacharacter scanning needed since `asyncio.create_subprocess_exec` does not interpret shell syntax.

### Effort estimate

Small. One new file (~100 lines), edits to `__init__.py` and DESIGN.md, one test file (~150 lines). No protocol changes. No scheduler changes.

---

## Feature 2: Pipeline-aware tool composition (`tool.compose`)

### Problem

When tool A calls tool B as an implementation detail (e.g., a custom tool that internally reads a file using the read tool's logic), the call goes through the full pipeline wrapper: active-task check, secret substitution, trace callback, secret scrubbing, mutation tracking. This means:

1. The inner call appears as a separate trace entry (double attribution)
2. Secret substitution runs twice (once on A's args, once on B's args within A)
3. The active-task check runs again (unnecessary overhead)
4. Mutation tracking fires for B even though A should get attribution

### Solution

A `compose` method (or standalone function) that calls a tool's raw `execute` function, bypassing the pipeline wrapper.

### Affected files

| File | Action | Notes |
|------|--------|-------|
| `tool/src/orxt/tool/_pipeline.py` (lines 20-73) | Edit | Store raw execute reference on the wrapped tool, or expose a compose function |
| `protocols/src/orxt/protocols/_tool.py` (lines 10-16) | Possibly edit | If adding a field to Tool dataclass |
| `tool/src/orxt/tool/__init__.py` | Edit | Export the compose mechanism |
| `tool/tests/test_pipeline.py` | Edit | Add compose tests |

### How the pipeline currently works

`wrap_tool_with_pipeline` (lines 20-73 of `_pipeline.py`) takes a `Tool` and returns a new `Tool` with the same `name`, `description`, `parameters`, but a different `execute` function (`wrapped_execute`). The original `tool.execute` is captured in the closure (line 49: `result = await tool.execute(effective_args)`).

The problem: after wrapping, there is no way to get back to the original `execute`. The wrapped `Tool` is a frozen dataclass -- `tool.execute` is the wrapped version.

### Design options

**Option A: Add a `raw_execute` field to the Tool dataclass.**
- Change `Tool` in `protocols/_tool.py` to include `raw_execute: Callable | None = None`.
- `wrap_tool_with_pipeline` sets `raw_execute` to the original `execute` before wrapping.
- Callers use `tool.raw_execute(args)` to bypass the pipeline.
- Problem: `Tool` is a protocol-level type shared across modules. Adding an optional field to it leaks pipeline implementation details into the protocol layer.

**Option B: Store the raw execute in a side registry.**
- `wrap_tool_with_pipeline` returns both the wrapped tool and stores the original execute in a `dict[str, Callable]` keyed by tool name.
- The registry is passed alongside the tool list.
- Problem: adds another data structure to thread through.

**Option C: Standalone `compose` function that unwraps.**
- A function `compose(tool: Tool, args: dict) -> str` that checks if the tool's execute is a known wrapper and calls the inner function.
- Problem: introspecting closures is fragile.

**Option D: Wrapper stores original on itself as an attribute.**
- The `wrapped_execute` function object (a closure) gets an attribute: `wrapped_execute._raw_execute = tool.execute`.
- `compose(tool, args)` checks `hasattr(tool.execute, '_raw_execute')` and calls it.
- This is Pythonic (functions are objects with assignable attributes) and doesn't touch the protocol.

**Recommended: Option D.** In `_pipeline.py` after defining `wrapped_execute`:

```python
wrapped_execute._raw_execute = tool.execute  # type: ignore[attr-defined]
```

Then a public function:

```python
async def compose(tool: Tool, args: dict[str, Any]) -> str:
    """Call a tool's raw execute, bypassing the pipeline.
    
    Use when tool A calls tool B as an implementation detail.
    B's execution is not traced, not scrubbed, not mutation-tracked.
    Attribution goes to the outer tool A.
    """
    raw = getattr(tool.execute, '_raw_execute', None)
    if raw is not None:
        return await raw(args)
    # Not wrapped -- call directly
    return await tool.execute(args)
```

**Trace attribution:**
- The outer tool A is already being traced by the pipeline wrapper.
- The inner tool B's compose call produces no trace entry.
- Duration of B is included in A's total duration (since A's pipeline measures wall-clock time around `tool.execute(effective_args)`, and A's execute internally awaits compose(B)).
- This is the correct behavior: the consumer sees one tool call (A) with its total duration.

**API surface:** `await compose(tool_b, args)` from inside tool A's execute function. Tool A has access to tool B because both are in the same tool registry passed to the session.

### Open questions

1. Should `compose` be a free function or a method? A free function keeps `Tool` frozen and protocol-clean. A method would require either unfreezing `Tool` or adding a mixin. Free function is better.
2. How does tool A get a reference to tool B? Currently, tool constructors don't receive the tool registry. The consumer would need to either (a) close over the registry when constructing tool A, or (b) pass it as a constructor parameter. This is a consumer-side concern, not an orxt framework concern.

### Effort estimate

Very small. ~20 lines of new code in `_pipeline.py`, one new export, a few test cases. No protocol changes.

---

## Feature 3: First-class orchestrator task type with `await_task`

### Problem

Currently, orchestrator-like agents can create subtasks via `create_task`, but they cannot wait for a specific subtask to complete and get its result. The agent creates subtasks, calls `end_task`, and the scheduler verifies all subtasks completed. But the agent never sees the subtask results within its own session.

The desired pattern: an orchestrator agent with a persistent multi-turn session creates a child task, calls `await_task(task_id)`, and its session is suspended while the child runs. When the child completes, the orchestrator's session resumes with the child's result injected as a tool response.

### How `_execute_agent_task` currently works

`_execute_agent_task` (lines 533-890 of `_executor.py`):

1. Resolves agent definition, model, transport (lines 540-598)
2. Creates a session with lifecycle tools (start_task, end_task) via `_create_agent_session` (lines 589-594)
3. Builds the prompt with constraints, notepad, prior failure context (lines 597-635)
4. Calls `_run_session` which does `async for event in session.send(prompt)` (lines 648-665)
5. `session.send()` is a single call that runs the full tool-calling loop until the LLM returns an end_turn

The key constraint: `session.send()` is a single async generator that runs to completion. The transport's `send()` method (lines 54-248 of `_transport.py`) loops calling the API, processing tool calls, and re-calling until the LLM stops calling tools. The session layer (lines 57-132 of `_session.py`) wraps this with transcript tracking.

For an orchestrator, we need:
- Multiple `session.send()` calls (multi-turn)
- The ability to suspend between turns
- The child task runs during the suspension
- The child's result is fed back as input to the next `session.send()` call

### Where the changes go

| File | Action | Notes |
|------|--------|-------|
| `protocols/src/orxt/protocols/_task.py` | Edit | Add `SUSPENDED` to `TaskState` enum (line 24-33) |
| `scheduler/src/orxt/scheduler/_executor.py` | Major edit | New `_execute_orchestrator_task` method; modify `_build_lifecycle_tools` to include `await_task`; add orchestrator loop |
| `tool/src/orxt/tool/_task_tools.py` | Edit | Add `make_await_task_tool` constructor; extend `TaskSchedulerRef` protocol |
| `tool/src/orxt/tool/__init__.py` | Edit | Export new tool |
| `tool/DESIGN.md` | Edit | Document await_task tool |
| `scheduler/DESIGN.md` | Edit | Document orchestrator task type |
| `scheduler/tests/test_executor.py` | Edit | Add orchestrator tests |
| `session/src/orxt/session/_session.py` | No change | `send()` already supports multi-turn via session_id |

### How `await_task` would signal suspension

The core mechanism: `await_task` is a tool that, when called by the LLM, triggers a special control flow in the scheduler.

**Approach: sentinel return value.**

The `await_task` tool's execute function:
1. Validates the task_id exists and is a child of the orchestrator's active task
2. Records the child task_id for the scheduler
3. Returns a sentinel value (e.g., a JSON object `{"__await_task__": true, "task_id": "..."}`)

But this doesn't actually suspend. The transport will include the tool result in the conversation and call the API again, expecting the LLM to continue.

**Better approach: exception-based control flow.**

Define a new exception `TaskSuspend(task_id)` in the tool module. When `await_task`'s execute raises `TaskSuspend`, the transport's tool-call loop catches it and stops iterating. The session's `send()` generator yields a special event and terminates.

But `Transport.send()` (line 186-220) catches `ToolError` and formats it as an error result. It does not catch arbitrary exceptions -- they would propagate up and abort the session.

**Best approach: break out of the tool-calling loop.**

The actual mechanism:

1. `await_task` execute function sets a flag on the scheduler: `self._pending_suspend[session_id] = child_task_id`
2. `await_task` returns a normal success string: `"Awaiting task {child_task_id}. Session will resume with the result."`
3. The transport sends this result to the LLM and the LLM responds
4. But we need the session to stop after this tool call completes, before the LLM's response is sent back to the API

This is the crux: the tool-calling loop in `Transport.send()` is:
```python
while True:
    # call API
    # process response blocks
    if tool_use_blocks:
        # execute tools, add results to history
        continue  # loop again
    # no more tool calls -> break
    break
```

The loop continues as long as the LLM requests tool calls. When the LLM calls `await_task`, the tool executes and returns a result. The result goes into `history`. Then `continue` sends the history back to the API. The LLM sees the await_task result and either calls more tools or stops.

**The problem:** there is no way to break out of the transport loop from inside a tool. The tool returns a string; the transport decides whether to continue.

**Solution: the orchestrator does NOT use the transport's built-in tool loop.** Instead:

1. Add a new task type detection in `execute_task` (line 447-489): if the task spec has a new flag `orchestrator=True`, dispatch to `_execute_orchestrator_task`.

2. `_execute_orchestrator_task` runs a manual multi-turn loop:
   ```python
   session = create_session(...)
   # Initial send
   result = await self._run_session(session, prompt, ...)
   
   while self._has_pending_suspend(session_id):
       child_task_id = self._pop_pending_suspend(session_id)
       # Transition orchestrator to SUSPENDED
       self._task_states[task_id] = TaskState.SUSPENDED
       # Execute the child task (full lifecycle)
       child_result = await self.execute_task(child_spec, task_id)
       # Resume the orchestrator
       self._task_states[task_id] = TaskState.ACTIVE
       # Send child result as next message
       resume_msg = f"Task {child_task_id} completed: {child_result.output}"
       result = await self._run_session(session, resume_msg, ...)
   ```

3. The `await_task` tool's execute sets the pending suspend flag AND tells the LLM to stop calling tools. The transport will see the tool result, send it to the API, and the LLM should respond with text (not more tool calls) because the tool result says "session will suspend."

**But there's still the loop problem.** If the LLM calls `await_task` alongside other tools in the same response, those other tools execute too. And the transport continues to the API with all results.

**More robust solution: make `await_task` the ONLY tool available to orchestrators that triggers suspension, and handle it at the Session/executor level, not the Transport level.**

The cleanest approach:

1. In `_execute_orchestrator_task`, intercept the `session.send()` generator. `session.send()` yields events including `ToolUse` events. When a `ToolUse` event with `tool_name="await_task"` is yielded, the executor knows a suspension was requested.

2. But `session.send()` has already executed the tool (transport executes tools inline at lines 186-220) and continued the loop. By the time the executor sees the `ToolUse` event, the transport has already sent the result back to the API.

**The fundamental architectural issue:** The transport owns the tool-calling loop. Tools execute inside the transport's `send()`. There is no hook for the scheduler to intercept mid-loop.

**Proposed solution: transport extension.**

Add an optional `on_tool_result` callback to `Transport.send()`. After each tool executes, the callback is invoked. If it returns a sentinel (e.g., `"suspend"`), the transport breaks out of the while loop, yielding the current state.

```python
# In Transport.send():
result_text = await tool.execute(tool_input)
if on_tool_result is not None:
    action = on_tool_result(tool_name, result_text)
    if action == "suspend":
        # Don't continue the loop -- break after adding results
        history.append({"role": "user", "content": tool_results})
        break  # exits while True
```

This requires changes to `Transport.send()` signature:

| File | Change |
|------|--------|
| `transport/src/orxt/transport/_transport.py` (line 54) | Add `on_tool_result: Callable | None = None` parameter |
| `session/src/orxt/session/_session.py` (line 71) | Pass `on_tool_result` through to transport |

Then the orchestrator executor:
1. Creates a session with an `on_tool_result` callback that watches for `await_task` tool calls
2. When the callback fires, it sets a flag and returns `"suspend"`, causing the transport to break
3. The session's `send()` generator terminates (yielding what it has so far)
4. The executor checks the flag, runs the child task, then calls `session.send()` again with the child's result
5. The transport resumes from where it left off (history is preserved in `self._sessions[session_id]`)

### State preserved during suspension

The session object (`Session` class) persists between `send()` calls:
- `self._session_id` -- preserved (set on first send, reused)
- `self.total_input_tokens` etc. -- preserved (cumulative counters)
- `self.turn_count` -- incremented on each send
- `self._transport._sessions[session_id]` -- the conversation history, preserved

The transport's `self._sessions` dict (line 52 of `_transport.py`) holds the full message history keyed by session_id. When `send()` is called again with the same session_id, it appends to the existing history. This means multi-turn already works at the transport level.

### Changes to TaskSpec

Add to `TaskSpec` (protocols/_task.py, line 36-66):
```python
orchestrator: bool | None = None  # New field
```

### Changes to TaskState

Add to `TaskState` (protocols/_task.py, lines 24-33):
```python
SUSPENDED = "suspended"
```

### The `await_task` tool specification

```python
_AWAIT_TASK_PARAMETERS = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "The child task ID to wait for."
        }
    },
    "required": ["task_id"],
    "additionalProperties": False,
}
```

The tool:
1. Validates task_id is a child of the orchestrator's active task
2. Validates the child task is in a terminal state OR not yet started
3. If not yet started, sets the pending-suspend flag
4. Returns a message describing what will happen

### Open questions

1. **Can an orchestrator await multiple tasks sequentially?** Yes -- the outer loop in `_execute_orchestrator_task` continues as long as suspensions are pending.
2. **Can an orchestrator await tasks in parallel?** Not with this design. Each `await_task` suspends and runs one child. Parallel await would need a different primitive (e.g., `await_all_tasks`). This is a future extension.
3. **What happens if the orchestrator calls other tools AND await_task in the same turn?** The transport executes tools sequentially (lines 143-220). The `on_tool_result` callback fires after each tool. When `await_task` fires, the transport breaks after adding all tool results so far. The remaining tools in that batch are NOT executed. This is correct -- suspension is immediate.
4. **Budget tracking across suspension.** The orchestrator's cost accumulates across all its `send()` calls. The child task's cost is tracked separately. Both are accounted for.
5. **What if the child task fails?** The child's `TaskResult` is returned regardless. The resume message includes the failure information. The orchestrator decides what to do (retry, skip, escalate).
6. **Transport changes are cross-cutting.** Adding `on_tool_result` to `Transport.send()` affects the transport module, which is used by all sessions. The parameter is optional and defaults to None (no behavior change for existing code).

### Effort estimate

Large. Touches protocols, transport, session, scheduler, and tool modules. Requires careful handling of the transport loop interruption. Estimated at 3-5 implementation sessions.

---

## Dependencies between the three features

- Feature 1 (shell tool) and Feature 2 (compose) are independent of each other and of Feature 3.
- Feature 2 (compose) is useful for Feature 1's implementation: if a shell tool wanted to internally compose with the read tool to validate a path, it could use compose. But this is not a hard dependency -- the shell tool can work standalone.
- Feature 3 (orchestrator) is independent of Features 1 and 2 architecturally, but an orchestrator agent would likely use shell tools and composed tools in practice.
- **Recommended implementation order:** Feature 2 (smallest, foundational), then Feature 1 (small, standalone), then Feature 3 (largest, most cross-cutting).

## Architectural concerns

1. **Transport loop interruption (Feature 3):** The `on_tool_result` callback approach modifies a core loop. Alternative: keep the transport loop unchanged and have `await_task` raise a custom exception that propagates through Session.send() to the executor. The executor catches it, runs the child, and calls send() again. This avoids modifying Transport but means the session's async generator terminates abnormally. The callback approach is cleaner but more invasive.

2. **Security surface of shell tool (Feature 1):** Even with binary whitelisting, the allowed binaries can be used maliciously (e.g., `uv pip install evil-package`). The whitelist is a capability boundary, not a security boundary. The consumer bears responsibility for the choice of allowed binaries.

3. **Compose bypasses all safety (Feature 2):** By design, compose skips secret scrubbing, active-task checks, and mutation tracking. A tool using compose to call a write tool bypasses stale-write detection. This is intentional (the outer tool takes responsibility) but should be documented clearly.

4. **DESIGN.md explicitly says "Does not ship a bash/shell tool" (line 333).** Feature 1 is not a bash tool -- it's a scoped, whitelist-only command runner using subprocess_exec. But the DESIGN.md language should be updated to clarify the distinction.
