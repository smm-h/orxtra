# Protocols Module Design

Shared type definitions for the boundaries between modules. Foundation layer -- zero intra-workspace dependencies. Every module that crosses a boundary imports from here.

## Responsibility

Define the types, enumerations, and protocol interfaces that multiple modules share. This module contains only data definitions and protocol classes -- no logic, no I/O, no database access.

## Execution

The core abstraction: a unit of work that can be a script, an agent, or a workflow. Used for pre-checks, post-checks, and any context where "run this thing and tell me the result" is needed.

### Execution Types

An Execution is one of three variants:

| Variant | What it is | Typical use |
|---|---|---|
| Script | A Python callable path (`module:function`) | Mechanical checks: lint passes, file exists, tests pass |
| Agent | An agent definition name + task prompt | Intelligent checks: code review, architecture consistency |
| Workflow | A task tree (concrete or goal-oriented) | Complex checks: full integration test workflow, audit |

```python
@dataclass(frozen=True)
class ScriptExecution:
    callable: str  # "module:function" dotted path with colon separator

@dataclass(frozen=True)
class AgentExecution:
    agent: str              # agent definition name
    task: str               # task prompt (supports {variable} substitution)
    variables: list[str]    # variable names to inject
    block_threshold: str    # "critical" | "major" | "minor" | "nit"

@dataclass(frozen=True)
class WorkflowExecution:
    name: str
    description: str
    tasks: list[TaskSpec]         # concrete subtask list
    postchecks: list[Execution]   # checks on the workflow itself
    budget: Decimal | None        # USD budget for this execution

Execution = ScriptExecution | AgentExecution | WorkflowExecution
```

Script executions are synchronous Python callables. Agent executions spawn a read-only agent via consult. Workflow executions create a nested task tree and run it to completion. All three produce a `CheckResult`.

### Execution Results

Every Execution produces a `CheckResult`:

```python
@dataclass(frozen=True)
class CheckResult:
    passed: bool
    message: str
    details: dict | None = None
    fix: Callable | None = None  # auto-fix callable (script executions only)
```

Agent executions produce an additional `CheckVerdict` with structured findings:

```python
@dataclass(frozen=True)
class CheckVerdict:
    verdict: str               # "pass" or "fail"
    issues: list[CheckIssue]
    criteria_review: list[CriterionReview]
    summary: str

@dataclass(frozen=True)
class CheckIssue:
    severity: str              # "critical" | "major" | "minor" | "nit"
    file: str | None
    line_range: tuple[int, int] | None
    description: str
    blocking: bool             # derived: severity >= block_threshold

@dataclass(frozen=True)
class CriterionReview:
    criterion: str
    met: bool
    evidence: str
```

Severity levels in order: `critical > major > minor > nit`. The `blocking` field on each issue is derived mechanically: `issue.severity >= block_threshold`. A verdict's `verdict` field is `"pass"` only if zero issues have `blocking = true`.

## Task Lifecycle

A task is the universal unit of work. Tasks nest recursively. A workflow is a task containing subtasks. A run is the root task.

### Task States

```
created -> prechecking -> active -> postchecking -> completed
                |                       |
                v                       v
         precheck_failed         postcheck_failed -> active (agent retries end_task)
                                        |
                                        v
                                    escalated (agent cannot satisfy postchecks)
```

Additional transitions:
- Any state -> `cancelled` (parent abort, budget exhaustion, timeout)
- `active` -> `postchecking` is triggered by the agent calling `end_task`
- `postcheck_failed` -> `active` happens when the agent receives the failure and continues working
- `postcheck_failed` -> `escalated` happens when the agent gives up or exhausts attempts

### Task Specification

The schema for declaring a task, whether in a workflow TOML or via `create_task` tool call:

```python
@dataclass(frozen=True)
class TaskSpec:
    name: str
    prechecks: list[Execution]
    postchecks: list[Execution]

    # Exactly one of: agent + task_prompt, callable, subtasks, gate, or decision_point
    agent: str | None = None             # agent definition name
    task_prompt: str | None = None       # prompt template with {variable} placeholders
    callable: str | None = None          # Python callable path for function tasks
    subtasks: list[TaskSpec] | None = None  # nested task list (this task is a workflow)
    gate: str | None = None              # event name to wait for (blocks until event or timeout)
    decision_point: bool | None = None   # pauses execution and sends event to Overseer

    variables: list[str] = field(default_factory=list)
    depends_on: list[str] | None = None
    depends_on_previous: bool | None = None
    category: str | None = None          # override agent's default category
    timeout: int | None = None           # seconds, required for agent tasks
    context_refinement: bool | None = None  # required for agent tasks
    retry: int = 0
    retry_resume: bool | None = None     # required when retry > 0
    retry_inject_failure: bool | None = None  # required when retry > 0
    for_each: str | None = None
    for_each_abort_on_failure: bool | None = None  # required when for_each is set
    output_schema: str | None = None     # JSON Schema path
    budget: Decimal | None = None        # per-task USD budget
    write_paths: list[str] | None = None
    on_success: str | None = None        # callable path
    pre_retry: str | None = None         # callable path
```

A task must declare exactly one execution mode: `agent` + `task_prompt` (agent task), `callable` (function task), `subtasks` (workflow/composite task), `gate` (wait for named event), or `decision_point` (pause and invoke Overseer). Every task must declare either `depends_on` or `depends_on_previous`. Both missing or both present is a hard error.

### Task Context

Provided to checks, callbacks, and injected into agent prompts:

```python
@dataclass(frozen=True)
class TaskContext:
    variables: dict[str, Any]
    run_id: UUID
    task_name: str
    task_id: UUID
    attempt: int
    prior_attempts: list[AttemptSummary] | None
    notepad_content: str
    parent_task_id: UUID | None
    nesting_depth: int

@dataclass(frozen=True)
class AttemptSummary:
    attempt: int
    output: str | None
    check_results: list[CheckResult]
    duration_seconds: float
```

### Task Result

What a completed or failed task produces:

```python
@dataclass(frozen=True)
class TaskResult:
    output: str | None             # agent text output
    structured_output: dict | None  # validated JSON if output_schema was set
    check_results: list[CheckResult]

@dataclass(frozen=True)
class EscalationPayload:
    task_name: str
    task_id: UUID
    agent_name: str | None
    attempts: int
    failed_checks: list[CheckResult]
    agent_summary: str
    context: TaskContext
```

When a task fails and the agent cannot satisfy postchecks, the `EscalationPayload` is delivered to the parent task's agent as a structured message. The parent decides: create a new subtask to fix the problem, adjust constraints, escalate further, or abort.

## Scheduler-Overseer Events

Event types the scheduler sends to the Overseer. Each event is a message in the Overseer's persistent session. The Overseer responds by taking actions via its tools.

```python
@dataclass(frozen=True)
class RunStarted:
    intent: str
    config_snapshot: dict

@dataclass(frozen=True)
class TaskFailed:
    task_id: UUID
    task_name: str
    payload: EscalationPayload

@dataclass(frozen=True)
class TaskEscalated:
    task_id: UUID
    task_name: str
    from_child_task_id: UUID
    payload: EscalationPayload

@dataclass(frozen=True)
class BudgetThresholdCrossed:
    workflow_id: UUID
    budget_usd: Decimal
    spent_usd: Decimal
    threshold_pct: float

@dataclass(frozen=True)
class BudgetExhausted:
    workflow_id: UUID

@dataclass(frozen=True)
class InboxAnswered:
    item_id: UUID
    assumed_option: str
    actual_answer: str
    contradicts: bool

@dataclass(frozen=True)
class InboxRejected:
    item_id: UUID
    rejection_reason: str  # why the human rejected the options

@dataclass(frozen=True)
class StructuralAdvisory:
    task_id: UUID
    observation: str  # what the scheduler noticed
    suggestion: str   # what it recommends
    # e.g., "nodes A5, B7 are read-only -- consider front-loading them"
    # e.g., "phase 8 should happen before phase 3 because phase 3 depends on its output"

@dataclass(frozen=True)
class HealthDegraded:
    event_type: str
    failure_rate: float
    threshold: float
```

The scheduler formats these as structured messages to the Overseer. The Overseer's system prompt describes how to handle each event type. The Overseer's action tools are the menu of possible responses.

### Advisory Messages

`StructuralAdvisory` events are observations the scheduler makes about the task tree: read-only tasks that could be front-loaded, dependency ordering improvements, parallelization opportunities. These are **advisory, never autonomous mutations**. The scheduler presents the observation and suggestion; the owning agent decides whether to act on it. The agent should not blindly follow mechanical advice -- it has context the scheduler does not.

## Overseer Action Tool Schemas

Parameter and return types for the Overseer's action tools. Schemas are defined here; implementations live in the overseer module.

### create_workflow

Create a goal-oriented task tree. A workflow agent decomposes it.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Workflow name |
| `description` | string | yes | What this workflow accomplishes |
| `goals` | array of strings | yes | Goal descriptions for the workflow agent |
| `postchecks` | array of Executions | no | Checks run when the workflow completes |
| `budget` | number | no | USD budget for this workflow |

Returns: `workflow_id` (UUID) or validation error.

### create_task

Create a concrete subtask within the current active task.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Task name |
| `agent` | string | yes | Agent definition name |
| `task_prompt` | string | yes | Task prompt template |
| `prechecks` | array of Executions | no | Checks before the agent can start |
| `postchecks` | array of Executions | no | Checks the agent must satisfy to finish |
| `variables` | object | no | Variables for prompt substitution |
| `timeout` | integer | yes | Max wall-clock seconds |
| `budget` | number | no | Per-task USD budget |
| `write_paths` | array of strings | no | File paths this task may write |
| `context_refinement` | boolean | yes | Whether the Overseer refines context |
| `category` | string | no | Override agent's default category |
| `retry` | integer | no | Max retry count (default 0) |
| `retry_resume` | boolean | conditional | Required if retry > 0 |
| `retry_inject_failure` | boolean | conditional | Required if retry > 0 |
| `depends_on` | array of strings | no | Sibling task names that must complete first |

Returns: `task_id` (UUID) or validation error.

### record_decision

Record a decision in the Overseer's memory.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `decision_type` | string | yes | What kind of decision (e.g., "retry_strategy", "budget_reallocation") |
| `choice` | object | yes | Structured choice details |
| `rationale` | string | no | Why this decision was made |

Returns: `decision_id` (UUID).

### add_constraint

Add a constraint to the run.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `text` | string | yes | Constraint description |
| `tier` | string | yes | `"mechanical"` or `"advisory"` |

Returns: `constraint_id` (UUID).

### record_assumption

Record an assumption. Optionally create an inbox item for human review.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `text` | string | yes | What is being assumed |
| `scope` | string | yes | `"understanding"`, `"decomposition"`, or `"task"` |
| `create_inbox_item` | boolean | yes | Whether to escalate to the human inbox |

Returns: `assumption_id` (UUID), `inbox_item_id` (UUID or null).

### create_inbox_item

Create a human inbox item.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `question` | string | yes | The question for the human |
| `options` | array | yes | Options considered |
| `assumed_option` | string | yes | Which option the Overseer assumed |
| `work_proceeding` | string | yes | What work continues under the assumption |
| `contradiction_impact` | string | yes | What happens if the human picks differently |
| `tags` | array of strings | no | Tags for triage |
| `deadline` | string | no | ISO timestamp deadline for response |
| `answer_event` | string | no | Event name fired when answered (for gate tasks to await) |

Returns: `item_id` (UUID).

The `options` array must always include the most correct solution regardless of effort. Never omit the hard-but-right option in favor of easier alternatives.

### write_lesson

Write to the cross-run knowledge base.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `text` | string | yes | The learned fact |
| `relevance_tags` | array of strings | yes | Tags for retrieval |
| `permanent` | boolean | yes | True for consumer knowledge |
| `source_file` | string | no | File path for staleness detection |

Returns: `lesson_id` (UUID).

### update_workflow_status

Update the Overseer's assessment of a workflow's health.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `workflow_id` | string | yes | Workflow UUID |
| `current_step` | string | no | Current task name |
| `health` | string | yes | `"healthy"`, `"degraded"`, or `"failing"` |

Returns: ok.

## Overseer Interaction Model

The scheduler and Overseer interact through a persistent session:

1. The scheduler detects an event (task failed, budget crossed, run started, etc.)
2. The scheduler formats the event as a structured message and sends it to the Overseer's session
3. The Overseer's tool-call loop runs: it inspects files (read/grep/glob), researches via consult, and takes actions via its action tools
4. The Overseer produces a text response summarizing what it did
5. The scheduler verifies the Overseer's actions:
   - Did created workflows/tasks pass schema validation?
   - Do new constraints contradict existing ones?
   - Is this the same action taken for the same failure last time? (repetition check)
   - Is budget allocation proportional to remaining work?
6. If verification passes: the event is handled. The scheduler proceeds.
7. If verification fails: the scheduler sends the failure details back to the Overseer as a follow-up message in the same session. The Overseer must fix its actions.
8. This loop runs up to N times per event. If the Overseer cannot satisfy verification after N attempts, the scheduler enters degraded mode for that event type.

The Overseer's text response is a summary for the trace, not a structured output. The real actions are the tool calls. The scheduler inspects the database state (new workflows, constraints, assumptions) to determine what the Overseer did.

## Check Context for Scripts

Script-type Executions receive a `CheckContext`:

```python
@dataclass(frozen=True)
class CheckContext:
    variables: dict[str, Any]
    agent_output: str | None   # None for pre-checks
    run_id: UUID
    session_id: str | None
    task_name: str
    task_id: UUID
    attempt: int
    parent_task_id: UUID | None
```

The function signature for script checks:

```python
async def my_check(ctx: CheckContext) -> CheckResult:
    ...
```

## Check Context for Agent Checks

Agent-type Executions receive a `CheckAgentContext` injected as template variables:

```python
@dataclass(frozen=True)
class CheckAgentContext:
    task: str              # the original task prompt given to the agent
    agent_output: str      # the agent's text output (post-checks only)
    mechanical_results: str # formatted results from script checks that ran before this
    task_name: str
    attempt: int
    notepad: str
```

Task variables are also injected with a `var_` prefix. A task variable `target` becomes `{var_target}`.

## Callback Signatures

`on_success` and `pre_retry` callbacks:

```python
async def on_success(ctx: TaskContext) -> None:
    """Runs after postchecks pass. Non-fatal: exceptions are logged."""
    ...

async def pre_retry(ctx: TaskContext) -> None:
    """Runs before a retry attempt. For state cleanup. Exceptions abort the retry."""
    ...
```

## Files

| File | Contents |
|---|---|
| `_tool.py` | `Tool` frozen dataclass: name, description, parameters (JSON Schema dict), execute (async callable). `ToolError` exception. Shared across transport, tool, and scheduler. |
| `_execution.py` | `ScriptExecution`, `AgentExecution`, `WorkflowExecution`, `Execution` union type. `CheckResult`, `CheckVerdict`, `CheckIssue`, `CriterionReview`. |
| `_task.py` | `TaskSpec`, `TaskContext`, `TaskResult`, `AttemptSummary`, `EscalationPayload`. Task lifecycle state enum. |
| `_events.py` | `RunStarted`, `TaskFailed`, `TaskEscalated`, `BudgetThresholdCrossed`, `BudgetExhausted`, `InboxAnswered`, `InboxRejected`, `StructuralAdvisory`, `HealthDegraded`. |
| `_tools.py` | Parameter and return schemas for all Overseer action tools. Pydantic models for validation. |
| `_checks.py` | `CheckContext`, `CheckAgentContext`, `CheckExecutor` protocol (interface for spawning consult agents and creating subtasks, injected by scheduler). Callback type aliases for `on_success` and `pre_retry`. |

## What This Module Does NOT Do

- Does not implement any logic (that is scheduler/, overseer/, verify/)
- Does not import from any other orxt module
- Does not define agent definitions, tool constructors, or database operations
- Does not contain prompt text
