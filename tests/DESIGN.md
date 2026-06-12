# Tests

One test module per source module. Tests live here permanently, not in `/tmp/`.

## Files

| File | Tests for |
|---|---|
| `test_agent.py` | Agent TOML loading, schema validation, category resolution, prompt substitution (strict-both-ways). |
| `test_tool.py` | Tool dataclass, constructors, JSON Schema argument validation, spawn/consult/notepad tool behavior. |
| `test_transport.py` | Provider protocol conformance, tool-call loop (mock provider), event type construction, session ID management. |
| `test_providers.py` | AnthropicProvider and OpenAIProvider request/response serialization. Mock HTTP, no live API calls. |
| `test_pipeline.py` | Pipeline TOML loading, dependency graph construction, topological sort, cycle detection, step schema validation (required fields, mutual exclusivity). |
| `test_executor.py` | Pipeline execution: step ordering, parallel execution, retry logic, timeout enforcement, cancellation, crash recovery. Uses mock transport. |
| `test_verify.py` | Mechanical verification callable dispatch, VerifyAgentContext construction, var_ prefixing, pass/fail parsing. |
| `test_notepad.py` | JSONL read/write, entry schema validation, notepad formatting for prompt injection. |
| `test_session.py` | Session lifecycle, cost accumulation, handoff detection threshold, handoff execution. |
| `test_trace.py` | Run directory creation, artifact writing/reading, query API, crash recovery round-trip. |
| `test_graph.py` | Dependency graph edge cases: diamonds, independent branches, cycles, depends_on_previous resolution. |
