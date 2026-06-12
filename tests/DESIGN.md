# Tests

One test module per source module. Tests live here permanently, not in `/tmp/`.

## Files

| File | Tests for |
|---|---|
| `test_overseer.py` | Decision protocol registry, context assembly, structured output parsing, SQLite memory read/write, constraint accumulation, assumption tracking. |
| `test_overseer_health.py` | Health metric tracking, degraded mode entry/exit per decision type, threshold detection. |
| `test_overseer_handoff.py` | Session handoff trigger, summary production, transcript persistence, new session creation with UUID. |
| `test_overseer_inbox.py` | Human inbox queue, assumption recording, contradiction detection. |
| `test_overseer_learning.py` | Cross-run knowledge base persistence, staleness detection, expiry, consumer knowledge file loading (.md and .toml), permanent vs transient entries. |
| `test_scheduler.py` | Workflow validation (schema + sanity-check), event loop, step execution ordering, parallel execution, budget enforcement, mechanical constraint checking, pause/resume. |
| `test_scheduler_checkpoint.py` | Crash recovery: checkpoint writing, state restoration, in-progress step handling. |
| `test_scheduler_state.py` | Workflow state: typed variable validation, scratch lifecycle, variable name collisions, step output propagation (_output, _text, _result). |
| `test_scheduler_steps.py` | Decision point steps (Overseer invocation + workflow mutation), gate steps (event wait + timeout), workflow steps (child spawn, wait vs fire-and-forget). |
| `test_scheduler_services.py` | Service lifecycle: start, health-check, stop, runtime detail injection, cleanup on crash. |
| `test_scheduler_locks.py` | File-lock registry: claim/release, conflict detection, blocking, scope expansion escalation. |
| `test_scheduler_crossflow.py` | Cross-workflow data flow: reads_from resolution, blocking on unavailable fields, read-only enforcement. |
| `test_scheduler_context.py` | Agent step context assembly: three layers, Overseer refinement, diff storage, token budget filling. |
| `test_scheduler_actions.py` | Post-step actions: on_success callable dispatch (non-fatal), pre_retry callable dispatch (state cleanup before retry). |
| `test_agent.py` | Agent TOML loading, schema validation, category resolution, prompt substitution (strict-both-ways). |
| `test_agent_includes.py` | Prompt composition: {include:filename.md} resolution, nesting, circular detection, missing target errors. |
| `test_tool.py` | Tool dataclass, constructors, JSON Schema argument validation, spawn/consult/notepad tool behavior. |
| `test_transport.py` | Provider protocol conformance, tool-call loop (mock provider), event type construction, session ID management. |
| `test_providers.py` | AnthropicProvider and OpenAIProvider request/response serialization. Mock HTTP, no live API calls. |
| `test_verify.py` | Mechanical verification callable dispatch, VerifyAgentContext construction, var_ prefixing, pass/fail parsing, fixable failures (fix callable + re-verify cycle, single iteration). |
| `test_notepad.py` | JSONL read/write, entry schema validation, notepad formatting for prompt injection. |
| `test_session.py` | Session lifecycle, cost accumulation. |
| `test_trace.py` | Run directory creation, artifact writing/reading, query API, crash recovery round-trip, RunReport schema, context diff storage. |
| `test_graph.py` | Dependency graph edge cases: diamonds, independent branches, cycles, depends_on_previous resolution. |
| `test_errors.py` | Error taxonomy: classification from exit codes, stderr patterns, error messages. |
