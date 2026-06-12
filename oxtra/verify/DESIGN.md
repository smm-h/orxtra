# Verification Module Design

How agent work is checked.

## Core Axiom

Verification is mechanical, not requested. The scheduler runs verification after every step completion, regardless of what the agent reports. The agent cannot skip verification, opt out, or influence what gets checked.

## Two Tiers

**Tier 1: Mechanical Verification (Python callables)**

- A Python function that takes a context dict and returns a `VerifyResult`
- Runs first, acts as a gate
- Fast, deterministic, testable
- Examples: file exists, file is valid Python, test suite passes, DB has rows, product count > 0

**Tier 2: Semantic Verification (verification agent)**

- A read-only agent spawned via `consult` (cannot write, edit, or spawn)
- Runs only if mechanical verification passes
- Slow, non-deterministic, costs tokens
- Examples: code quality review, architecture consistency check, correctness review
- The verification agent receives: the original task, the agent's output, and the mechanical verification results

## Verify Result

```python
@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    message: str           # human-readable explanation
    details: dict | None   # structured data (optional) -- e.g., test results, file list
```

## Callable Specification

Verification callables are referenced in pipeline TOML as dotted Python paths with a colon separator:

```
verify = "myproject.verify:step_complete"
```

This means: `from myproject.verify import step_complete`. The function signature is:

```python
async def step_complete(ctx: VerifyContext) -> VerifyResult:
    ...
```

Where `VerifyContext` contains:

| Field | Type | Description |
|---|---|---|
| `variables` | `dict` | The step's runtime variables (domain, crawl_dir, etc.) |
| `agent_output` | `str` | The text output from the agent |
| `run_dir` | `Path` | Path to the current pipeline run directory |
| `session_id` | `str` | The agent's session ID |
| `step_name` | `str` | The pipeline step name |
| `attempt` | `int` | Attempt number (1-indexed: 1 for first attempt, 2 for first retry, etc.) |

## Verification Agent

The verification agent is a full agent definition (TOML + .md prompt file), not a framework-constructed template. The consuming project defines it like any other agent -- with its own prompt, category, and tool whitelist. The executor invokes it via `consult`, injecting a verification context struct as template variables.

When `verify_agent` is set on a pipeline step:

1. The executor loads the named agent definition.
2. The executor builds a `VerifyAgentContext` and passes its fields as template variables to the agent's prompt:

```python
@dataclass(frozen=True)
class VerifyAgentContext:
    task: str              # the original task prompt given to the agent
    agent_output: str      # the agent's text output
    mechanical_results: str # formatted mechanical verification results (or empty if no mechanical verify)
    step_name: str         # the pipeline step name
    attempt: int           # attempt number (1-indexed)
    notepad: str           # formatted notepad content (learnings, decisions, issues)
```

Step variables are also injected, but namespaced with a `var_` prefix to prevent collisions with framework fields. A step variable named `target` becomes `{var_target}` in the verification agent's prompt. This means a step variable named `task` does not collide with the framework's `{task}` field.

3. The executor spawns the agent via `consult` (read-only mode). The agent's `.md` prompt uses `{task}`, `{agent_output}`, `{mechanical_results}`, `{var_target}`, etc. as placeholders. Variable strictness applies -- the template must reference every field in the context, and the context provides every variable the template needs.
4. The verification agent's response is parsed for a pass/fail determination.
5. If the verification agent reports failure, it counts as a step failure (triggering retry if available).

## Key Design Decisions

**Callables are synchronous checks, not hooks.** They run at a specific point in the pipeline (after agent completion), not as middleware that intercepts events. There is no "before step" or "during step" verification -- only "after step."

**No built-in verification functions.** oxtra provides the framework for running verification, but defines zero verification functions. Those are the consuming project's domain.

**Verification agents are read-only.** They are spawned via `consult`, which mechanically removes write/edit/bash/spawn tools. A verification agent cannot modify files, run commands, or delegate work. It can only read and report.

**Verification failure = step failure.** There is no "warning" or "soft fail." If verification fails, the step failed. The pipeline decides what to do (retry or abort) based on the step's retry policy.

## Files

| File | Contents |
|---|---|
| `_types.py` | `VerifyResult`, `VerifyContext` (for mechanical callables), `VerifyAgentContext` (for verification agents). All frozen dataclasses. |
| `_runner.py` | `run_mechanical_verify(callable_path, ctx)` -- imports and calls the verification function. `run_agent_verify(agent_name, verify_ctx, executor)` -- builds `VerifyAgentContext`, invokes the verification agent via `consult`, parses pass/fail from response. |

## What This Module Does NOT Do

- Does not define what to verify (that's the consuming project's verification functions)
- Does not implement retry logic (that's `scheduler/`)
- Does not track verification history across runs
- Does not provide a "fix it" capability -- verification reports problems, it doesn't solve them
