# Tests

One test module per source module. Tests live here permanently, not in `/tmp/`.

All tests require a PostgreSQL instance (provided via service container in CI, local PG for development). Test fixtures create and destroy test databases per test session.

## Files

| File | Tests for |
|---|---|
| `test_overseer.py` | Decision protocol registry, context assembly, structured output parsing, PG memory read/write, constraint accumulation, assumption tracking. |
| `test_overseer_health.py` | Health metric tracking, degraded mode entry/exit per decision type, threshold detection. |
| `test_overseer_handoff.py` | Session handoff trigger, summary production, transcript persistence, new session creation with UUID. |
| `test_overseer_inbox.py` | Human inbox item creation, tag auto-injection, status lifecycle (pending/answered/skipped/expired), answer event firing. |
| `test_overseer_learning.py` | Cross-run knowledge base persistence, staleness detection, expiry, consumer knowledge file loading (.md and .toml), permanent vs transient entries. |
| `test_overseer_autonomy.py` | Autonomy level action-type mapping, action-gating enforcement, mid-run level changes. |
| `test_scheduler.py` | Workflow validation (schema + sanity-check), event loop, step execution ordering, parallel execution, budget enforcement (USD), mechanical constraint checking, pause/resume. |
| `test_scheduler_recovery.py` | Three-pass crash recovery: reclaim interrupted, re-evaluate blocked, clean orphaned. Advisory lock reclamation. Idempotency under repeated crashes. |
| `test_scheduler_state.py` | State machine transitions (legal and illegal), typed variable validation, scratch lifecycle, variable name collisions, step output propagation (_output, _text, _result). |
| `test_scheduler_steps.py` | Decision point steps (Overseer invocation + workflow mutation), gate steps (event wait + timeout + inbox answer events), workflow steps (child spawn, wait vs fire-and-forget). |
| `test_scheduler_services.py` | Service lifecycle: start, health-check, stop, runtime detail injection, cleanup on crash. |
| `test_scheduler_locks.py` | File-lock registry: claim/release, conflict detection, blocking, scope expansion escalation. |
| `test_scheduler_crossflow.py` | Cross-workflow data flow: reads_from resolution, blocking on unavailable fields, read-only enforcement. |
| `test_scheduler_context.py` | Agent step context assembly: three layers, Overseer refinement, diff storage, token budget filling. |
| `test_scheduler_actions.py` | Post-step actions: on_success callable dispatch (non-fatal), pre_retry callable dispatch (state cleanup before retry). |
| `test_scheduler_write_safety.py` | Per-path write queue serialization, stale-write detection, transient replay, atomic replace. Race condition scenarios with concurrent agents. |
| `test_agent.py` | Agent TOML loading, pydantic schema validation (strict, extra=forbid), category resolution, prompt substitution (strict-both-ways). |
| `test_agent_includes.py` | Prompt composition: {include:filename.md} resolution, nesting, circular detection, missing target errors. |
| `test_tool.py` | Tool dataclass, constructors, JSON Schema argument validation, spawn/consult/notepad tool behavior. |
| `test_tool_path.py` | Path canonicalization: symlink traversal, boundary escapes, read root vs write scope enforcement. |
| `test_tool_preview.py` | No-truncation preview: threshold detection, head/tail generation, full=true escalation guard, custom previewer callable. |
| `test_tool_write_queue.py` | Write queue: serialization correctness, stale-write detection hash tracking, transient vs deterministic failure classification, replay from recorded args. |
| `test_transport.py` | Provider protocol conformance, tool-call loop (mock provider), event type construction, session ID management, retry policy enforcement. |
| `test_providers.py` | AnthropicProvider and OpenAIProvider request/response serialization. Mock httpx, no live API calls. |
| `test_verify.py` | Verification chain execution: ordered dispatch, short-circuit on failure, fix-then-re-verify cycle (single iteration). |
| `test_verify_verdict.py` | Structured verdict validation, severity levels, verify_block_threshold application, blocking derivation, VerifyAgentContext construction with var_ prefixing. |
| `test_notepad.py` | PG-backed entry read/write, entry schema validation, notepad formatting for prompt injection. |
| `test_session.py` | Session lifecycle, token accumulation, transcript persistence to PG, cross-restart resumption. |
| `test_trace.py` | PG schema creation, artifact writing/reading via TraceWriter/reader, state machine enforcement, LISTEN/NOTIFY event delivery, UUIDv7 ordering, advisory lock lifecycle, RunReport generation. |
| `test_trace_recovery.py` | Three-pass recovery round-trip: create interrupted state, run recovery, verify correct transitions. |
| `test_graph.py` | Dependency graph edge cases: diamonds, independent branches, cycles, depends_on_previous resolution. |
| `test_errors.py` | Error taxonomy: classification from exit codes, stderr patterns, error messages. |
| `test_services.py` | Service functions: run lifecycle, inbox operations, trace queries, validation, config. Integration tests against PG. |
| `test_cli.py` | CLI argument parsing, command dispatch, output formatting (table and JSON). |
| `test_mcp.py` | MCP server tool registry, JSON-RPC dispatch, response format. |
| `conftest.py` | Shared fixtures: PG test database creation/teardown, asyncpg pool, mock transport/provider, sample agent/workflow TOML files. |
