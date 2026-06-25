# fire_blocking() crashes when called from within an async event loop

## Problem

`fire_blocking()` in `services/src/orxtra/services/_events.py` uses bare `asyncio.run()`, which raises `RuntimeError: asyncio.run() cannot be called from a running event loop` when called from sync code that happens to be executing inside an async context (e.g., a sync integration handler called from an async web server).

## Current implementation

```python
def fire_blocking(...) -> UUID:
    return asyncio.run(fire_event(pool, run_id, event_name, payload, source))
```

## Expected behavior

Detect whether an event loop is already running. If so, dispatch the async call to a background thread instead of using `asyncio.run()`. If no loop is running, `asyncio.run()` is fine.

## Why it matters

An external system migrating to orxtra's event bus has integration handlers (webhook processors, CLI commands) that fire events from sync code. These handlers often run inside an async web server (ASGI). The current implementation makes `fire_blocking()` unusable in that context, which is its primary use case.

## Reference pattern

The external system's current implementation handles both cases:

1. No running event loop → `asyncio.run(fire_event(...))`
2. Running event loop → `loop.run_in_executor(None, lambda: asyncio.run(fire_event(...)))` with a dedicated connection (not the pool, to avoid blocking pool connections from the async side)

The DSN caching pattern is also worth considering: the external system caches the database URL after first read to avoid repeated env var lookups in hot paths.
