# Trace Module Design

Persistence layer for pipeline runs. Owns the run directory structure, writes the historical record during execution, and provides query access to completed sessions and steps.

## Responsibility

Write and read the artifacts of a pipeline run: per-step results, transport event logs, session transcripts, and the final pipeline result. This module is the single owner of the run directory format. Pipeline and session modules call into trace to persist data; they do not write to the run directory directly.

## Run Directory Structure

```
{data_dir}/runs/{pipeline_name}/{timestamp}/
    result.json                    # Final pipeline result
    steps/
        {step_name}.json           # Per-step result
    events/
        {step_name}.jsonl          # Transport events per step
    transcripts/
        {session_id}.jsonl         # Full session transcript per session
    notepad/
        learnings.jsonl            # Cross-agent learnings
        decisions.jsonl            # Cross-agent decisions
        issues.jsonl               # Cross-agent issues
```

The run directory is created by the pipeline executor at the start of a run. The trace module provides functions to write each artifact type. The directory persists after the run completes or fails -- it is the permanent record.

## Artifacts

### Pipeline Result (`result.json`)

Written once at the end of the run (success or failure).

```python
@dataclass(frozen=True)
class PipelineResult:
    passed: bool
    pipeline_name: str
    started_at: str           # ISO timestamp
    finished_at: str          # ISO timestamp
    duration_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    steps: list[StepSummary]  # ordered by execution
    failure: str | None       # step name that caused abort, or None
```

### Step Result (`steps/{step_name}.json`)

Written after each step completes (success or failure). This is the artifact that enables crash recovery -- on restart, the caller can pass previously completed step results to skip them.

```python
@dataclass(frozen=True)
class StepResult:
    step_name: str
    passed: bool
    agent_output: str | None       # text output from the agent (or None for function steps)
    structured_output: dict | None # validated JSON if output_schema was set
    verification_result: VerifyResult | None
    session_id: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_seconds: float
    retries_used: int
    attempt: int                   # final attempt number
```

### Transport Events (`events/{step_name}.jsonl`)

One JSONL line per transport event, written as events stream in. Every `StepStart`, `Text`, `ToolUse`, `StepFinish`, and `Error` event is logged with a timestamp.

```json
{"ts": "2026-06-12T10:30:00Z", "type": "ToolUse", "tool_name": "bash", "input": {"command": "ls"}, "output": "file1.txt\nfile2.txt", "status": "success"}
{"ts": "2026-06-12T10:30:01Z", "type": "StepFinish", "input_tokens": 1200, "output_tokens": 450, "cost_usd": 0.0032}
```

For `for_each` steps, events from all iterations are interleaved in a single file, each tagged with an `iteration` field.

### Session Transcripts (`transcripts/{session_id}.jsonl`)

The full conversation history for a session: every user message, assistant response, tool call, and tool result, with per-turn token counts. Written incrementally as the session progresses.

```json
{"turn": 1, "role": "user", "content": "Investigate example.com..."}
{"turn": 1, "role": "assistant", "content": "I'll start by...", "tool_calls": [{"name": "bash", "input": {"command": "curl..."}}]}
{"turn": 1, "role": "tool_result", "tool_name": "bash", "content": "<!DOCTYPE html>..."}
{"turn": 1, "usage": {"input_tokens": 800, "output_tokens": 350}}
```

This is the artifact that enables session handoff -- when a new session receives an old session's UUID, it queries the transcript store to access the full history.

## Query API

```python
def read_step_result(run_dir: Path, step_name: str) -> StepResult | None:
    """Read a completed step result, or None if the step hasn't run."""
    ...

def read_transcript(run_dir: Path, session_id: str) -> list[dict]:
    """Read a session's full transcript as a list of entries."""
    ...

def query_transcript(run_dir: Path, session_id: str, query: str) -> list[dict]:
    """Search a session transcript for entries matching a query (tool name, content substring, turn number)."""
    ...

def read_pipeline_result(run_dir: Path) -> PipelineResult | None:
    """Read the final pipeline result, or None if the run hasn't finished."""
    ...

def list_runs(data_dir: Path, pipeline_name: str) -> list[Path]:
    """List all run directories for a pipeline, ordered by timestamp."""
    ...
```

## Crash Recovery

The trace module enables crash recovery by persisting step results incrementally. The pipeline executor writes each `StepResult` to `steps/{step_name}.json` as soon as the step completes. On restart:

1. The caller reads existing step results from a previous run directory via `read_step_result`
2. The caller passes them to the pipeline executor as `completed_steps`
3. The executor skips steps that have a completed result and proceeds from the first incomplete step

This is not automatic resume -- the caller decides which run directory to read from and which results to trust. The trace module provides the persistence; the caller provides the policy.

## Session Handoff Support

When the session module triggers a handoff (context approaching 90% of the model's window):

1. The session asks the current agent to produce a summary
2. The session module calls `trace.write_transcript()` to ensure the transcript is fully persisted
3. A new session is created with the summary as initial context
4. The new session receives the old session's UUID
5. The new agent can call `trace.read_transcript()` or `trace.query_transcript()` to access the old session's full history

The trace module does not decide when to trigger handoff (that's session/). It provides the storage and retrieval that makes handoff possible.

## Key Design Decisions

**Single owner of the run directory.** No other module writes directly to the run directory. Pipeline calls `trace.write_step_result()`. Session calls `trace.write_transcript()`. The notepad tool calls `trace.write_notepad_entry()`. This prevents format drift and conflicting writes.

**JSONL for streaming artifacts.** Events and transcripts use JSONL because they are written incrementally as the run progresses. Step results and the pipeline result use plain JSON because they are written once.

**Timestamps on everything.** Every event, transcript entry, and result includes an ISO timestamp. This enables post-hoc analysis of timing, bottlenecks, and parallelism behavior.

**No cleanup.** The trace module never deletes run directories. Cleanup is the caller's responsibility. Run directories accumulate until explicitly removed.

## What This Module Does NOT Do

- Does not decide when to write (that's pipeline/ and session/)
- Does not decide what to verify (that's verify/)
- Does not enforce retention policies or disk limits
- Does not provide real-time streaming of events to external systems (it writes to disk)
- Does not implement the session handoff decision logic (that's session/)
