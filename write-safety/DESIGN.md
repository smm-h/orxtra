# Write Safety Module Design

Concurrency-safe file mutation primitives. Foundation layer -- zero intra-workspace dependencies.

## Responsibility

Prevent four classes of file mutation failure:

| Mechanism | Failure class it prevents |
|---|---|
| Atomic replace (temp + fsync + rename) | Torn/half-written files on crash |
| Per-path write queue | Interleaved concurrent writes; racy read-modify-replace in edit |
| Transient-only replay | Re-paying output tokens for OS hiccups (never for deterministic errors) |
| Stale-write detection | Silent lost updates between agents |

## Atomic Replace

Every file write goes through: write to temp file in same directory -> fsync -> rename over target. A crash at any point leaves either the old file or the new file, never a torn partial.

## Per-Path Write Queue

An `asyncio.Lock` per canonical file path. Concurrent writes to the same path are serialized. Edit's read-modify-replace is race-free because both the read and the write happen under the lock.

```python
class WriteQueue:
    async def acquire(self, path: Path) -> None: ...
    def release(self, path: Path) -> None: ...
    async def with_lock(self, path: Path, fn: Callable) -> Any: ...
```

## Stale-Write Detection

The write queue tracks the content hash of each file at each agent session's last read.

- When an agent reads a file, the current content hash is recorded for that session
- When an agent writes/edits a file, the write queue checks:
  1. Has this agent ever read this file? (hard error if not, for existing files)
  2. Has the file changed since the agent's last read? (hard error if yes)
- New file creation needs no prior read
- The hash comparison happens under the per-path lock, before the write

```python
class StaleWriteTracker:
    def record_read(self, session_id: str, path: Path, content_hash: str) -> None: ...
    def check_write(self, session_id: str, path: Path, current_hash: str) -> None: ...
```

## Transient-Only Replay

When a write fails for a transient OS reason (EIO, EBUSY), the write queue replays from the recorded tool arguments. The agent is never asked to re-emit content.

Deterministic errors (scope violation, hunk mismatch, stale-write detection) always surface to the agent. They are never retried.

## Files

| File | Contents |
|---|---|
| `_atomic.py` | Atomic file replace: temp + fsync + rename. |
| `_queue.py` | `WriteQueue`: per-path asyncio locks. |
| `_stale.py` | `StaleWriteTracker`: content hash tracking per session, stale-write checks. |
| `_replay.py` | Transient error detection and replay logic. |

## What This Module Does NOT Do

- Does not decide what to write (that is the tool module)
- Does not manage file paths or scoping (that is the tool module's path enforcement)
- Does not track which tools were called (that is the scheduler's active-task enforcement)
- Does not interact with the database
