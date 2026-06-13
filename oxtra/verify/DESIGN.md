# Verification Module Design

How agent work is checked.

## Core Axiom

Verification is mechanical, not requested. The scheduler runs verification after every step completion, regardless of what the agent reports. The agent cannot skip verification, opt out, or influence what gets checked.

## Two Tiers

**Tier 1: Mechanical Verification (ordered chain of Python callables)**

- An ordered list of Python functions, each taking a context and returning a `VerifyResult`
- Run in declared order, cheapest first, short-circuiting on first failure
- Fast, deterministic, testable
- Examples: file exists, file is valid Python, linter passes, test suite passes, product count > 0

**Tier 2: Semantic Verification (verification agent)**

- A read-only agent spawned via `consult` (cannot write, edit, or spawn)
- Runs only if all mechanical verification callables pass
- Returns a structured verdict against a framework-defined schema
- Slow, non-deterministic, costs tokens
- Examples: code quality review, architecture consistency check, correctness review

## Verify Result

```python
@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    message: str           # human-readable explanation
    details: dict | None   # structured data (optional) -- e.g., test results, file list
    fix: Callable | None   # optional auto-fix callable -- see "Fixable Failures" below
```

## Verification Chain

The `verify` field on a workflow step is an ordered list of Python callable paths:

```toml
verify = [
    "myproject.verify:format_check",
    "myproject.verify:lint_check",
    "myproject.verify:type_check",
    "myproject.verify:test_suite"
]
```

The scheduler runs them in order. On the first failure:
- If the failed result has a `fix` callable, run it, then re-verify that single callable (once -- no recursion)
- If re-verification passes, continue the chain
- If re-verification fails or no fix callable exists, the step fails (triggering retry if available)

Convention: cheapest first (format -> lint -> typecheck -> tests). The framework documents this convention but does not enforce ordering.

## Callable Specification

Verification callables are referenced as dotted Python paths with a colon separator:

```
verify = ["myproject.verify:step_complete"]
```

This means: `from myproject.verify import step_complete`. The function signature is:

```python
async def step_complete(ctx: VerifyContext) -> VerifyResult:
    ...
```

Where `VerifyContext` contains:

| Field | Type | Description |
|---|---|---|
| `variables` | `dict` | The step's runtime variables |
| `agent_output` | `str` | The text output from the agent |
| `run_id` | `uuid.UUID` | Current run ID |
| `session_id` | `str` | The agent's session ID |
| `step_name` | `str` | The workflow step name |
| `attempt` | `int` | Attempt number (1-indexed) |

## Verification Agent

The verification agent is a full agent definition (TOML + .md prompt file), not a framework-constructed template. The consuming project defines it like any other agent. The executor invokes it via `consult`, injecting a verification context struct as template variables.

### Structured Verdict Schema

Verification agents must return structured output against a framework-defined verdict schema. This is enforced like any `output_schema` -- the transport validates the output, retrying on mismatch.

```python
@dataclass(frozen=True)
class VerifyVerdict:
    verdict: str               # "pass" or "fail"
    issues: list[VerdictIssue]
    criteria_review: list[CriterionReview]
    summary: str

@dataclass(frozen=True)
class VerdictIssue:
    severity: str              # "critical", "major", "minor", "nit"
    file: str | None
    line_range: tuple[int, int] | None
    description: str
    blocking: bool             # derived from severity vs verify_block_threshold

@dataclass(frozen=True)
class CriterionReview:
    criterion: str
    met: bool
    evidence: str
```

### Severity and Blocking

Four severity levels: `critical | major | minor | nit`.

The `verify_block_threshold` field is **required** whenever `verify_agent` is set on a step. It is one of the four severity levels. Findings at the threshold severity or worse fail the step; findings below it are recorded in the verdict but do not block.

The `blocking` field on each `VerdictIssue` is derived mechanically: `issue.severity >= step.verify_block_threshold`.

The verdict's `verdict` field is `"pass"` only if zero issues have `blocking = true`.

### Context Injection

When `verify_agent` is set on a workflow step:

1. The executor loads the named agent definition.
2. The executor builds a `VerifyAgentContext` and passes its fields as template variables:

```python
@dataclass(frozen=True)
class VerifyAgentContext:
    task: str              # the original task prompt given to the agent
    agent_output: str      # the agent's text output
    mechanical_results: str # formatted results from the mechanical verify chain (or empty if none)
    step_name: str
    attempt: int
    notepad: str           # formatted notepad content
```

Step variables are also injected, namespaced with a `var_` prefix to prevent collisions. A step variable named `target` becomes `{var_target}`.

3. The executor spawns the agent via `consult` (read-only mode).
4. The agent's prompt uses `{task}`, `{agent_output}`, `{mechanical_results}`, `{var_target}`, etc. as placeholders. Variable strictness applies.
5. The structured verdict is validated and the blocking rule applied.
6. If the verdict fails (blocking issues exist), it counts as a step failure.

## Retry Failure Context

When a failed step retries with `retry_inject_failure = true`, the retrying agent receives the full structured failure picture:

- The `VerifyResult` from the mechanical chain (if that's where failure occurred)
- The `VerifyVerdict` from the semantic verification (if it ran)
- The agent's own previous output
- The attempt number

This is injected as structured data into the retry prompt context, not flattened into a message string.

## Fixable Failures

Some mechanical verification failures are mechanically fixable: linter formatting, import sorting, whitespace normalization. The `fix` field on `VerifyResult` supports this pattern.

When a mechanical verification callable returns a failed result with a non-None `fix` callable:

1. The scheduler calls `fix(ctx)` where `ctx` is the same `VerifyContext`
2. The scheduler re-runs that single verification callable
3. If re-verification passes, the chain continues to the next callable
4. If it fails, the step fails normally (triggering retry if available)
5. The fix-then-re-verify cycle runs at most once per callable. No recursion.

The fix callable is a separate action from the check. The verification function itself stays a check. The scheduler orchestrates the fix-then-re-verify cycle.

## Key Design Decisions

**Callables are synchronous checks, not hooks.** They run at a specific point in the workflow (after agent completion), not as middleware.

**No built-in verification functions.** oxtra provides the framework for running verification, but defines zero verification functions.

**Verification agents are read-only.** Spawned via `consult`, which mechanically removes write/edit/bash/spawn tools.

**Verification failure = step failure.** No "warning" or "soft fail." If verification fails, the step failed.

**Verification purity is not enforced.** The framework provides the `fix` callable mechanism for clean separation. However, consumers may write verification functions that fix issues and return passed. Both patterns are valid.

**Verification verdicts are structured, not free-text.** The framework defines the verdict schema. Verification agents cannot free-form their assessment -- they return typed findings.

## Files

| File | Contents |
|---|---|
| `_types.py` | `VerifyResult` (with optional `fix` callable), `VerifyContext` (for mechanical callables), `VerifyAgentContext` (for verification agents), `VerifyVerdict`, `VerdictIssue`, `CriterionReview`. All frozen dataclasses / pydantic models. |
| `_runner.py` | `run_verify_chain(callable_paths, ctx)` -- imports and calls each verification function in order, handles fix-then-re-verify, short-circuits on first non-fixable failure. `run_agent_verify(agent_name, verify_ctx, executor, block_threshold)` -- builds `VerifyAgentContext`, invokes the verification agent via `consult`, validates the structured verdict, applies blocking rule. |

## What This Module Does NOT Do

- Does not define what to verify (that's the consuming project)
- Does not implement retry logic (that's `scheduler/`)
- Does not track verification history across runs
- Does not decide retry strategy on failure
