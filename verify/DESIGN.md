# Verify Module Design

The check runner. Runs pre-checks and post-checks for tasks, where each check is an Execution: a script, an agent, or a workflow.

## Core Axiom

Verification is mechanical, not requested. The scheduler runs checks automatically at task boundaries (`start_task` and `end_task`). The agent cannot skip checks, opt out, or influence what gets checked. When post-checks fail, the agent is told why and can fix its work. When the agent cannot fix it, failure escalates to the parent task's agent.

## Execution Types

Each check is an Execution (defined in `orxt.protocols._execution`):

| Type | What it is | How it runs | Typical use |
|---|---|---|---|
| Script | Python callable path (`module:function`) | Import, call with `CheckContext`, return `CheckResult` | Mechanical: lint passes, file exists, tests pass |
| Agent | Agent definition name + task prompt | Spawn read-only agent via `consult`, structured verdict | Intelligent: code review, architecture consistency |
| Workflow | Task tree (concrete or goal-oriented) | Create subtasks, run to completion, aggregate results | Complex: full integration test workflow, audit |

## Check Result

Every Execution produces a `CheckResult`:

```python
@dataclass(frozen=True)
class CheckResult:
    passed: bool
    message: str
    details: dict | None = None
    fix: Callable | None = None  # auto-fix callable (script checks only)
```

## Check Runner

```python
async def run_checks(
    checks: list[Execution],
    ctx: CheckContext,
    phase: str,  # "pre" or "post"
    executor: CheckExecutor,  # injected by scheduler
) -> list[CheckResult]:
```

The `executor` parameter is a protocol interface (defined in `orxt.protocols`) that the scheduler injects. It provides the ability to spawn consult agents (for agent-type checks) and create subtasks (for workflow-type checks). The verify module defines what it needs; the scheduler provides the implementation. This avoids a dependency from verify to tool or scheduler.

### Pre-Check Behavior

Pre-checks run in order. On the first failure:
- If the failed result has a `fix` callable, run it, re-verify that single check (once -- no recursion)
- If re-verification passes, continue the chain
- If re-verification fails or no fix callable exists, the pre-check phase fails
- `start_task` returns the failure details to the agent

### Post-Check Behavior

Post-checks run in order. On the first failure:
- If the failed result has a `fix` callable, run it, re-verify (once)
- If re-verification passes, continue the chain
- If re-verification fails, the post-check phase fails
- `end_task` returns the failure details to the agent
- The agent can fix its work and call `end_task` again
- If the agent cannot satisfy post-checks, failure escalates to the parent

### Fix-Then-Re-Verify

Some checks are mechanically fixable: linter formatting, import sorting, whitespace normalization. The `fix` field supports this:

1. The check runner calls `fix(ctx)` where `ctx` is the `CheckContext`
2. Re-runs that single check
3. If it passes, continue the chain
4. If it fails, the check fails normally
5. Fix-then-re-verify runs at most once per check

The fix callable is separate from the check itself. Only script-type checks support auto-fix.

## Script Checks

Script-type Executions are Python callables:

```python
async def my_check(ctx: CheckContext) -> CheckResult:
    ...
```

Referenced as dotted paths with a colon separator: `"myproject.verify:lint_check"`.

`CheckContext` provides: `variables`, `agent_output` (None for pre-checks), `run_id`, `session_id`, `task_name`, `task_id`, `attempt`, `parent_task_id`. Defined in `orxt.protocols._checks`.

## Agent Checks

Agent-type Executions spawn a read-only agent via `consult`. The agent returns a structured `CheckVerdict`:

```python
@dataclass(frozen=True)
class CheckVerdict:
    verdict: str               # "pass" or "fail"
    issues: list[CheckIssue]
    criteria_review: list[CriterionReview]
    summary: str
```

### Severity and Blocking

Four severity levels: `critical > major > minor > nit`.

The `block_threshold` on the `AgentExecution` determines which findings block. Findings at the threshold severity or worse set `blocking = true`. The verdict is `"pass"` only if zero issues have `blocking = true`.

### Correctness Bias

Agent-type postchecks should be biased toward flagging shortcuts and suggesting the more correct approach. When a check agent finds that the work technically passes but a more correct approach exists, it should flag this as a `minor` or `major` issue (depending on impact), not silently accept. The check agent's prompt should encode: "if a more correct solution exists regardless of effort, flag it."

### Context Injection

The check runner builds a `CheckAgentContext` and passes its fields as template variables to the agent:
- `task`: the original task prompt
- `agent_output`: the agent's text output (post-checks only)
- `mechanical_results`: formatted results from script checks that ran before this
- `task_name`, `attempt`, `notepad`: runtime context

Task variables are injected with a `var_` prefix.

## Workflow Checks

Workflow-type Executions create a nested task tree and run it to completion. The workflow's result determines the check result:
- If the workflow completes: check passes
- If the workflow fails: check fails with the workflow's failure details

Workflow checks are expensive (they spawn full agent sessions) and should be used sparingly -- for complex verification that requires multiple coordinated steps.

## Escalation Payload

When an agent cannot satisfy post-checks, the check runner assembles an `EscalationPayload` (defined in `orxt.protocols._task`):
- Task name and ID
- Agent name
- Number of attempts
- All failed check results with details
- The agent's summary from its last `end_task` attempt
- The task context

This payload is delivered to the parent task's agent.

## Files

| File | Contents |
|---|---|
| `_types.py` | Re-exports from `orxt.protocols`: `CheckResult`, `CheckVerdict`, `CheckIssue`, `CriterionReview`, `CheckContext`, `CheckAgentContext`. |
| `_runner.py` | `run_checks(checks, ctx, phase)` -- the unified check runner. Handles script, agent, and workflow Executions. Fix-then-re-verify for scripts. Short-circuits on first non-fixable failure. |
| `_execution.py` | Execution dispatch: given an Execution spec, determine type and run it. Import and call for scripts. Spawn consult for agents. Create subtasks for workflows. |

## What This Module Does NOT Do

- Does not define what to check (that is the consuming project)
- Does not track check history across runs
- Does not decide retry strategy on failure (that is the parent task's agent)
- Does not implement the task lifecycle (that is the scheduler)
