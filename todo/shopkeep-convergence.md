# Shopkeep Convergence

Follow-up to `downstream-convergence.md`. Features 1 and 2 are implemented. Feature 3 remains design-only.

---

## Feature 3: Updated Design -- History Management Solution

### The problem restated

When an orchestrator agent calls `await_task(task_id)`, the transport's tool-call loop must break mid-execution so the scheduler can run the child task. When the child completes, the orchestrator's session resumes via `session.send(result_message)`. This creates two consecutive user-role messages in the conversation history, violating LLM API contracts that require strict role alternation (user -> assistant -> user -> assistant).

The sequence that creates the violation:

1. Orchestrator's `session.send(prompt)` runs. Transport enters tool-call loop.
2. LLM responds with tool calls including `await_task`. The `on_tool_result` callback returns `"suspend"`, breaking the loop.
3. Transport yields events and terminates the async generator. The conversation history at this point ends with a **user message** (the tool results that were appended before the break).
4. Child task executes to completion.
5. Executor calls `session.send(child_result_message)` to resume the orchestrator. This appends another **user message** to the history.
6. Two consecutive user messages. API rejects the request.

### Solution: synthetic assistant acknowledgment

Before resuming with the child result, inject a synthetic assistant message into the conversation history. This restores role alternation without requiring an actual API call.

**The exact message sequence:**

**Before suspension (inside the first `send()` call):**
```
history[0]: user    -> initial prompt
history[1]: assistant -> LLM response with tool_use blocks (including await_task)
history[2]: user    -> tool results (all executed tools, including await_task's result)
```

The `on_tool_result` callback fires after `await_task` executes. The transport appends the tool results as a user message (history[2]), then breaks out of the loop. The async generator terminates.

**During child execution:**
No changes to the orchestrator's history. The child runs in its own session.

**After child completes, before resume:**
The executor injects a synthetic assistant message:
```
history[3]: assistant -> "Task {child_task_id} suspended. Awaiting child result."
```

Then calls `session.send(child_result_message)`:
```
history[4]: user    -> "Task {child_task_id} completed. Result: {child_output}"
```

Role alternation is preserved: user(2) -> assistant(3) -> user(4).

**Implementation location:** The injection happens in `_execute_orchestrator_task` in the scheduler's `_executor.py`, NOT in the transport or session layers. The transport and session remain unaware of suspension semantics. The executor directly manipulates the transport's session history via `transport._sessions[session_id]`.

```python
# After child task completes, before calling session.send():
transport._sessions[session_id].append({
    "role": "assistant",
    "content": [{"type": "text", "text": f"Task {child_task_id} suspended. Awaiting child result."}],
})
```

This is an encapsulation violation (reaching into transport internals), but the alternatives are worse. Making the transport expose a `inject_message(session_id, message)` method is the cleaner version if this pattern proves durable.

### Handling skipped tools in multi-tool batches

When the LLM responds with multiple tool calls in a single turn (e.g., `[read_file, await_task, write_file]`), the transport processes them sequentially. The `on_tool_result` callback fires after each tool. When `await_task`'s callback returns `"suspend"`:

1. Tools executed before `await_task` in the batch have already run. Their results are recorded.
2. `await_task` itself has executed. Its result is recorded.
3. Tools after `await_task` in the batch have NOT executed.

**The transport must handle the skipped tools.** Two options:

**Option A: Report skipped tools as errors.**
After the suspend signal, the transport iterates remaining tool-use blocks and creates error results: `{"type": "tool_result", "tool_use_id": "...", "is_error": true, "content": "Tool execution skipped: session suspended by await_task"}`. These are appended to the user message (history[2]) alongside the successful results.

This is correct because the LLM needs to know those tools did not execute. When the session resumes, the LLM sees the error results and can decide whether to re-call them.

**Option B: Drop skipped tools silently.**
Only include results for tools that actually executed. The API requires a tool_result for every tool_use in the preceding assistant message.

**Option A is required.** The Anthropic API (and OpenAI) require a tool_result for every tool_use_id in the assistant's response. Missing tool_results cause API errors. So all tool-use blocks must have corresponding results, even if those results are errors.

**Implementation:** In the transport's tool-call loop, after the `on_tool_result` callback returns `"suspend"`:

```python
# Process remaining tool-use blocks with error results
for remaining_block in tool_use_blocks[current_index + 1:]:
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": remaining_block.id,
        "is_error": True,
        "content": "Tool execution skipped: session suspended by await_task.",
    })
```

### Token tracking across suspension

Token tracking works naturally because the `Session` object accumulates tokens from `StepFinish` events across all `send()` calls.

**First `send()` (before suspension):**
- `StepFinish` events from the API calls within the tool loop accumulate into `session.total_input_tokens`, `total_output_tokens`, etc.
- If the loop breaks before the final `StepFinish` (because suspension happens mid-loop), the tokens from the last API call are still captured -- `StepFinish` is emitted per API call, not per loop completion.

**Between `send()` calls (during child execution):**
- The child task's tokens are tracked in its own session. The orchestrator's token counters are unchanged.

**Second `send()` (after resume):**
- The resumed `send()` call adds to the existing accumulators. `turn_count` increments.
- The session's total reflects all API calls across both invocations.

**Budget enforcement:** The scheduler sums the orchestrator's session tokens and the child's session tokens independently. The orchestrator's budget covers its own LLM calls. The child's budget covers its own. If the orchestrator has a workflow-level budget, both contribute to it.

No special handling is needed. The existing accumulation model handles suspension correctly.

### The `on_tool_result` callback vs. alternatives

**`on_tool_result` is the right approach**, with one refinement.

The callback signature should be:

```python
on_tool_result: Callable[[str, str, str], str | None] | None = None
# Args: tool_name, tool_use_id, result_text
# Returns: None (continue) or "suspend" (break)
```

Including `tool_use_id` lets the executor correlate the suspension with the specific tool invocation, which is needed for correctly constructing the synthetic assistant message.

**Why not exception-based control flow (`TaskSuspend`):**
- Exceptions propagate through the async generator and terminate it abnormally. The caller must catch the exception, extract state, and manually clean up.
- The transport's `send()` generator uses `try/finally` for cleanup (e.g., yielding `Result` events). An uncaught exception bypasses this.
- The callback approach is cooperative: the transport breaks cleanly, yields its terminal events, and the generator ends normally.

**Why not sentinel return values from `await_task`:**
- The transport continues the loop regardless of what a tool returns. A sentinel value just becomes part of the conversation. The transport has no mechanism to inspect tool results and decide whether to continue.
- Adding that mechanism is equivalent to the callback approach but less explicit.

**Why not a separate `send_until_suspend()` method on Transport:**
- This duplicates the tool-call loop logic. The callback approach reuses the existing loop with a single injection point.

### Summary of changes needed for implementation

| Component | Change | Scope |
|-----------|--------|-------|
| `transport/_transport.py` | Add `on_tool_result` callback parameter to `send()`. After each tool executes, call the callback. If it returns `"suspend"`, fill remaining tool results with errors and break. | Medium |
| `session/_session.py` | Pass `on_tool_result` through to `transport.send()`. | Trivial |
| `protocols/_task.py` | Add `SUSPENDED` to `TaskState`. Add `orchestrator: bool` to `TaskSpec`. | Trivial |
| `scheduler/_executor.py` | New `_execute_orchestrator_task` method with the multi-turn loop, synthetic assistant injection, and `on_tool_result` callback. | Large |
| `tool/_task_tools.py` | New `make_await_task_tool` constructor. | Small |
| `tool/__init__.py` | Export `make_await_task_tool`. | Trivial |
