# claudestream migration gaps

Everything orxt needs to support, add, or fix to fully replace claudestream and absorb all its consumers. Excludes user-facing cosmetic concerns (color output, REPL, interactive CLI) since orxt's primary consumers are agents.

## Streaming

SSE streaming is implemented but force-disabled. `transport/_transport.py` sets `stream=False` on every request. Both `AnthropicProvider.parse_stream()` and `OpenAIProvider.parse_stream()` exist and parse SSE correctly, but they're never called.

This matters because:

- Long-running agent turns produce no output until the full response arrives -- no progress signal, no liveness detection
- Stuck detection (see below) depends on streaming; without it, a 10-minute API hang is indistinguishable from a slow response
- Token-level streaming enables early cancellation (abort a bad response before it finishes)
- Cost monitoring during a turn is impossible without streaming -- you only learn the cost after the response completes

## Liveness and stuck detection

No equivalent to claudestream's stuck detection. claudestream monitors per-readline timeout (30s health, 120s stuck) and transparently restarts the subprocess. orxt's httpx call has a flat 120s timeout -- if the API hangs at 119s, you wait the full duration. No graduated timeout, no transparent retry of hung requests, no "last event received at" tracking.

Needed: a liveness monitor that tracks time since last event (streaming) or time since request sent (non-streaming) and can cancel + retry hung requests with graduated thresholds.

## Tool schema convenience

No `@tool` decorator or auto-schema generation from type hints. Every orxt tool has a hand-written JSON Schema dict. The overseer tools use `Pydantic.model_json_schema()` which is better, but the pattern isn't generalized to agent-facing tools.

Options:

- A decorator that generates schemas from type hints (like claudestream's `@tool`)
- A standard pattern using Pydantic models for all tool parameter definitions (generalize what the overseer already does)
- Both (decorator for simple tools, Pydantic for complex ones)

## Tool auto-discovery

No `collect_tools(module)` equivalent. orxt constructs tool lists manually per agent/session. If a consumer has a module with 20 tool functions, they must enumerate them explicitly. claudestream's `collect_tools()` scans a module and collects all decorated functions automatically.

## One-shot / simple API

No simple "send prompt, get text" API. claudestream has `session.ask()` (returns text + metadata) and `print_prompt()` (one-liner: prompt in, text out). orxt requires constructing a Transport, Provider, Session, and iterating events to extract text.

This matters for lightweight consumers that don't need workflows -- scripts, one-off queries, simple integrations. `run_consult()` in the scheduler is close but requires a full scheduler context.

## Sync bridge

orxt is async-only. claudestream's `SyncSession` lets synchronous code use the library without managing an event loop. Some consumers (CLI scripts, Jupyter notebooks, simple integrations) expect a sync API. Whether this matters depends on whether all consumers are async.

## Forward compatibility

No `UnknownEvent` fallback. claudestream wraps unrecognized event types in `UnknownEvent` with the raw dict preserved, so consumers don't crash when the API adds new response types. orxt's providers parse known formats only -- a new Anthropic response field or content block type could cause a KeyError or silent data loss.

## Rate limit surfacing

Rate limits are retried but not surfaced as events. orxt's retry logic handles 429s internally with backoff. Consumers never learn they're being rate-limited -- no equivalent to claudestream's `RateLimit` event with `resets_at`, `rate_limit_type`, `utilization`. For budget-conscious consumers, knowing "you're at 80% utilization" is different from "request succeeded after a retry."

## Session resume

No session resume for individual LLM sessions. orxt maintains conversation history in-memory (`_sessions: dict[str, list]` in `_transport.py`), which means:

- If the Python process crashes, all in-flight conversation history is lost (transcripts are in PostgreSQL, but the transport's in-memory history is gone)
- No way to resume a multi-turn conversation after a restart
- The Overseer's session handoff creates a new session with a summary -- it doesn't resume

Needed: either persist conversation history to PostgreSQL so it can be reloaded on restart, or accept that crash = new session with summary (current Overseer approach) and extend this to all agent sessions.

## Google provider

The pricing table lists `google/gemini-2.5-flash` but no `GoogleProvider` implementation exists. Trying to use a Gemini model would fail at transport construction.

## Knowledge module gaps

Three dead code paths:

- `tags` parameter in `retrieve_knowledge()` is accepted but silently ignored (`_ = tags`)
- `config.max_retrieval_results` field exists but is never read
- `ContentHashCache` is in-memory only -- lost on restart, causing redundant re-ingestion

## Schema dual maintenance

The PostgreSQL schema exists in two places: `schema/orxt.toml` (pgdesign TOML) and `trace/_schema.py` (Python DDL). These are maintained manually in parallel. No migration framework, no automated sync check. A table added to one but not the other is a silent divergence.

## Budget hard enforcement

Unclear whether budget exhaustion actually stops execution. `BudgetExhausted` is emitted and sent to the Overseer, but the scheduler's response depends on the Overseer's action. If the Overseer is degraded, the fallback for `BudgetThresholdCrossed` is `"maintain_current_allocations"` -- which doesn't stop anything. Verify whether there's a hard stop when budget hits zero, or whether a degraded Overseer can let spending continue indefinitely.

## Per-event-type callbacks

Only a single catch-all trace callback. claudestream's `session.on(EventType, handler)` lets consumers register handlers for specific event types. orxt's TraceWriter fires one callback for all events -- consumers must filter by `event_type` string themselves.

## Context window awareness for agent sessions

Session handoff is Overseer-only. The Overseer has handoff logic at 90% context capacity, but regular agent sessions have no equivalent. A long-running agent task that fills its context window has no automatic recovery -- it hits the API's context limit and errors.

## Exec tool argument safety

`make_exec_tool` runs a fixed executable but doesn't sandbox arguments. The binary is fixed, but arguments are agent-controlled. An agent could pass malicious arguments to `pytest` or `uv` that escape the intended scope. There's path containment on cwd but no argument validation beyond what the JSON Schema enforces.

## Multi-edit tool

No multi-edit tool. claudestream derives FileEdit events for MultiEdit (batched edits to multiple files in one tool call). orxt has `edit` (single file, single find-and-replace). Batch editing requires multiple sequential tool calls, which costs more tokens.

## Concurrent write scope overlap

`FileLockRegistry` checks overlap but the overlap detection may be incomplete. Two tasks with write scopes `/src/a/` and `/src/a/b/` overlap (one is a prefix of the other). If the registry allows nested scopes, a child task could modify files its parent also writes to.

## Transport history growth

In-memory conversation history grows unbounded. `_transport.py` stores `self._sessions: dict[str, list[dict]]` with no eviction or compaction. A long multi-turn session accumulates every message in memory. Needed: either a cap with compaction (summarize older messages) or integration with context window tracking.

## Tool execution progress

No event when a tool is executing. orxt's `ToolUse` event includes output, status, error, and duration -- but only after the tool completes. For long-running exec tools (e.g., a 5-minute test suite), there's no progress signal until it finishes.

## Agent definition portability

Agent definitions can't declare per-agent budget or sandbox inline. claudestream's `.agent.json` includes budget, sandbox, model, MCP, and stream overrides directly on the agent definition. orxt's agent TOML only has name, description, prompt, category, and allow. Budget and write scope come from TaskSpec.

This means you can't look at an agent definition alone and know its full capability envelope. The separation is arguably correct (same agent, different constraints per task), but consider whether agents should be able to declare default resource boundaries that TaskSpec can override.
