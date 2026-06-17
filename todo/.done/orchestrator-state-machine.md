# Transport State Machine Refactor for Orchestrator await_task

## Decision

Solution 10 from the 10-solution analysis: refactor the transport's tool-call loop from a `while True` loop into an explicit state machine with first-class suspension.

## Why

The orchestrator pattern requires `await_task` — a tool that suspends the current LLM session, runs a child task to completion, then resumes the session with the child's result. This is impossible with the current monolithic while-loop transport without either blocking (deadlock risk) or history corruption (consecutive user messages).

A state machine makes suspension a first-class concept. States: `CALLING_API`, `EXECUTING_TOOLS`, `SUSPENDED`, `DONE`. The scheduler drives the state machine externally, stepping it forward. When `await_task` is encountered, the transport transitions to `SUSPENDED` with a continuation capturing: executed tools, their results, remaining tools, and a slot for the await result. The scheduler fills the slot and steps the machine forward.

## What changes

- `transport/_transport.py`: the ~165-line `send()` method becomes a state machine (~300 lines) with a `step()` function. The async iterator interface is preserved as a wrapper over the state machine for backward compatibility.
- `session/_session.py`: wraps the state machine instead of the raw async iterator. Session still exposes `send()` as an async iterator.
- `scheduler/_executor.py`: `_execute_orchestrator_task` drives the state machine, detecting SUSPENDED state, running children, injecting results, and resuming.
- `protocols/_task.py`: add `SUSPENDED` to TaskState, add `orchestrator: bool | None` to TaskSpec.
- `tool/_task_tools.py`: add `make_await_task_tool`.

## Multi-tool batch handling

`[read_file, await_task, write_file]`:
1. State machine enters EXECUTING_TOOLS
2. `read_file` executes → result captured
3. `await_task` encountered → state machine transitions to SUSPENDED, capturing read_file's result and write_file as remaining
4. Scheduler runs child task
5. Scheduler injects await_task result into the continuation
6. State machine resumes → executes `write_file` with its original args
7. All three results appended to history as one user message
8. State machine transitions to CALLING_API

History format is perfect: `assistant(tool_use) → user(all_tool_results)`. No synthetic messages.

## Conversation history

The state machine manages history as part of its state. No intermediate appends during SUSPENDED. When execution resumes, the full tool results are appended as one user message. Perfect alternation always.

## Effort estimate

Large. 3-5 sessions. The transport doubles in size. Every consumer of Transport.send() must be verified. Risk of regressions in a critical path.

## Status

Design decided. Not yet implemented. Features 1 (shell tool) and 2 (tool.compose) from the original shopkeep-convergence todo are implemented.
