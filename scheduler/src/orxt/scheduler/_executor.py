from __future__ import annotations

import asyncio
import errno
import importlib
import json
import logging
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import jsonschema
from orxt.notepad import NotepadEntry, format_notepad
from orxt.protocols._checks import CheckContext
from orxt.protocols._constraints import (
    EXPENSIVE_CONSTRAINTS,
    ConstraintKind,
)
from orxt.protocols._errors import ErrorCategory
from orxt.protocols._execution import CheckResult
from orxt.protocols._task import (
    AttemptSummary,
    EscalationPayload,
    TaskContext,
    TaskResult,
    TaskSpec,
    TaskState,
)
from orxt.protocols._tool import Tool, ToolError
from orxt.scheduler._events import EventRegistry
from orxt.scheduler._graph import (
    build_graph,
    find_parallel_groups,
    topological_sort,
)
from orxt.scheduler._locks import FileLockRegistry
from orxt.scheduler._validator import validate_task_tree
from orxt.session import Session, compute_cost_usd, create_session
from orxt.tool._pipeline import wrap_tools_for_session
from orxt.transport import Result, Usage
from orxt.verify import run_checks
from orxt.write_safety import StaleWriteTracker, WriteQueue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from uuid import UUID

    import asyncpg

    from orxt.agent import Agent
    from orxt.protocols._events import StructuralAdvisory
    from orxt.protocols._task import Execution
    from orxt.protocols._tools import (
        CreateTaskParams,
        CreateWaitForParams,
        CreateWorkflowParams,
    )
    from orxt.scheduler._overseer import (
        OverseerEvent,
        OverseerInterface,
    )
    from orxt.scheduler._types import WorkflowConfig
    from orxt.trace import TraceWriter
    from orxt.transport import Transport

_logger = logging.getLogger("orxt.scheduler")

_ACTIVE_STATES = frozenset({
    TaskState.ACTIVE,
    TaskState.PRECHECKING,
    TaskState.POSTCHECKING,
})

_NETWORK_ERRNOS = frozenset({
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    errno.ETIMEDOUT,
})


def classify_error(error: Exception) -> ErrorCategory:
    """Classify an exception into an error category."""
    if isinstance(
        error, (TimeoutError, asyncio.TimeoutError),
    ):
        return ErrorCategory.INFRA
    if (
        isinstance(error, OSError)
        and error.errno in _NETWORK_ERRNOS
    ):
        return ErrorCategory.INFRA
    if isinstance(error, json.JSONDecodeError):
        return ErrorCategory.PARSE
    if isinstance(
        error, (ModuleNotFoundError, ImportError),
    ):
        return ErrorCategory.BUILD_ENV
    if isinstance(error, AssertionError):
        return ErrorCategory.LOGIC
    return ErrorCategory.UNCLASSIFIED


def _task_type_for(task: TaskSpec) -> str:
    if task.decision_point:
        return "decision_point"
    if task.agent:
        return "agent"
    if task.callable:
        return "callable"
    if task.wait_for:
        return "wait_for"
    return "workflow"


class Scheduler:
    def __init__(  # noqa: PLR0913
        self,
        trace_writer: TraceWriter,
        transport_registry: dict[str, Transport],
        agents: dict[str, Agent],
        categories: dict[str, str],
        run_id: UUID,
        *,
        pool: asyncpg.Pool | None = None,
        overseer_interface: OverseerInterface | None = None,
        knowledge_dir: Path | None = None,
        model_context_limit: int = 200_000,
        knowledge_loader: Callable[[Path, Any, UUID], Awaitable[None]] | None = None,
        handoff_checker: Callable[[Any, int], Awaitable[bool]] | None = None,
        handoff_performer: Callable[[Any, Any, UUID], Awaitable[Any]] | None = None,
    ) -> None:
        self._trace_writer = trace_writer
        self._pool = pool
        self._transport_registry = transport_registry
        self._agents = agents
        self._categories = categories
        self._run_id = run_id
        self._overseer_interface = overseer_interface
        self._knowledge_dir = knowledge_dir
        self._model_context_limit = model_context_limit
        self._knowledge_loader = knowledge_loader
        self._handoff_checker = handoff_checker
        self._handoff_performer = handoff_performer
        self._active_tasks: dict[str, UUID] = {}
        self._task_states: dict[UUID, TaskState] = {}
        self._task_specs: dict[UUID, TaskSpec] = {}
        self._task_parents: dict[UUID, UUID | None] = {}
        self._task_children: dict[UUID, list[UUID]] = {}
        # Scoped per parent: parent_task_id -> {name -> value}
        self._task_outputs: dict[
            UUID | None, dict[str, str | None]
        ] = {}
        self._task_structured_outputs: dict[
            UUID | None, dict[str, dict[str, Any] | None]
        ] = {}
        self._task_results_meta: dict[
            UUID | None, dict[str, dict[str, Any]]
        ] = {}
        self._task_costs: dict[UUID, Decimal] = {}
        self._task_start_times: dict[UUID, float] = {}
        self._task_sessions: dict[UUID, Session] = {}
        self._running_tasks: set[asyncio.Task[Any]] = set()
        self._event_registry = EventRegistry()
        self._session_mutations: dict[str, bool] = {}
        self._write_queue = WriteQueue()
        self._stale_tracker = StaleWriteTracker()
        self._notepad_entries: list[NotepadEntry] = []
        self._active_constraints: list[tuple[str, str]] = []
        self._mechanical_constraints: list[tuple[str, str]] = []
        self._pending_end_task_message: dict[UUID, str] = {}
        self._file_lock_registry = FileLockRegistry()
        self._pending_advisories: list[
            dict[str, Any]
        ] = []
        self._paused = asyncio.Event()
        self._paused.set()  # Not paused initially

    async def run_consult(
        self,
        agent: str,
        question: str,
        variable_values: dict[str, str] | None = None,
    ) -> str:
        msg = "Overseer integration required"
        raise NotImplementedError(msg)

    async def run_workflow_check(
        self,
        execution: Execution,
    ) -> CheckResult:
        msg = "Overseer integration required"
        raise NotImplementedError(msg)

    def _get_scoped_outputs(
        self, parent_id: UUID | None,
    ) -> dict[str, str | None]:
        return self._task_outputs.setdefault(
            parent_id, {},
        )

    def _get_scoped_structured(
        self, parent_id: UUID | None,
    ) -> dict[str, dict[str, Any] | None]:
        return self._task_structured_outputs.setdefault(
            parent_id, {},
        )

    def _get_scoped_results_meta(
        self, parent_id: UUID | None,
    ) -> dict[str, dict[str, Any]]:
        return self._task_results_meta.setdefault(
            parent_id, {},
        )

    async def execute_workflow(
        self, config: WorkflowConfig,
    ) -> None:
        # Crash recovery: three-pass idempotent startup
        if self._pool is not None:
            from orxt.trace import (  # noqa: PLC0415
                acquire_run_lock,
                clean_orphaned,
                reclaim_interrupted,
                reevaluate_blocked,
            )

            await reclaim_interrupted(self._pool)
            await reevaluate_blocked(self._pool)
            await clean_orphaned(self._pool)
            await acquire_run_lock(
                self._pool, self._run_id,
            )

        # Load knowledge files at run start
        if (
            self._knowledge_dir is not None
            and self._knowledge_loader is not None
        ):
            await self._knowledge_loader(
                self._knowledge_dir,
                self._trace_writer,
                self._run_id,
            )

        errors = validate_task_tree(config)
        if errors:
            msg = (
                "Workflow validation failed: "
                f"{'; '.join(errors)}"
            )
            raise ValueError(msg)

        task_id_map: dict[str, UUID] = {}
        for task in config.tasks:
            task_id = await self._trace_writer.create_task(
                run_id=self._run_id,
                parent_task_id=None,
                name=task.name,
                task_type=_task_type_for(task),
            )
            task_id_map[task.name] = task_id
            self._init_task_state(task_id, task, parent=None)

        graph = build_graph(
            config.tasks, config.dependencies,
        )
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)

        # Analyze structural advisories for workflow
        task_ids = list(task_id_map.values())
        advisories = self._analyze_structural_advisories(
            task_ids,
        )
        if advisories:
            self._pending_advisories.extend(advisories)

        await self._send_pending_advisories()

        variables: dict[str, Any] = {}

        for group in groups:
            await self._paused.wait()
            tasks = [
                asyncio.create_task(
                    self.execute_task(
                        self._task_specs[task_id_map[name]],
                        None,
                        task_id=task_id_map[name],
                        variables=dict(variables),
                    ),
                )
                for name in group
            ]
            for t in tasks:
                self._running_tasks.add(t)
                t.add_done_callback(self._running_tasks.discard)
            results = await asyncio.gather(*tasks)

            for name, result in zip(
                group, results, strict=True,
            ):
                variables[f"{name}_output"] = (
                    result.structured_output
                    or result.output
                )
                variables[f"{name}_text"] = result.output
                all_passed = all(
                    cr.passed for cr in result.check_results
                )
                variables[f"{name}_result"] = {
                    "passed": all_passed,
                    "duration_seconds": 0.0,
                    "retries_used": 0,
                }

        # Coherence summary at run end
        await self._write_coherence_summary()

    def _init_task_state(
        self,
        task_id: UUID,
        task: TaskSpec,
        parent: UUID | None,
    ) -> None:
        self._task_states[task_id] = TaskState.CREATED
        self._task_specs[task_id] = task
        self._task_parents[task_id] = parent
        self._task_children[task_id] = []
        self._task_costs[task_id] = Decimal(0)

    async def execute_task(
        self,
        task: TaskSpec,
        parent_task_id: UUID | None,
        *,
        task_id: UUID | None = None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        await self._paused.wait()
        if task_id is None:
            task_id = await self._trace_writer.create_task(
                run_id=self._run_id,
                parent_task_id=parent_task_id,
                name=task.name,
                task_type=_task_type_for(task),
            )
            self._init_task_state(
                task_id, task, parent_task_id,
            )

        if task.for_each is not None:
            return await self._execute_for_each(
                task, task_id, parent_task_id, variables,
            )
        if task.callable is not None:
            return await self._execute_function_task(
                task, task_id, parent_task_id, variables,
            )
        if task.subtasks is not None:
            return await self._execute_composite_task(
                task, task_id,
            )
        if task.wait_for is not None:
            return await self._execute_wait_for_task(
                task, task_id,
            )
        if task.decision_point:
            return await self._execute_decision_point_task(
                task, task_id,
            )
        return await self._execute_agent_task(
            task, task_id, parent_task_id, variables,
        )

    async def _execute_decision_point_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult:
        """Execute a decision point task.

        Decision points are placeholder tasks that the Overseer
        handles in Phase 3. For now, just transition and complete.
        """
        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        await self._trace_writer.write_event(
            run_id=self._run_id,
            event_type="decision_point",
            data={
                "task_id": str(task_id),
                "task_name": task.name,
            },
            task_id=task_id,
        )

        self._complete_task(task_id, task.name, None)
        await self._trace_writer.transition_task(
            task_id, TaskState.COMPLETED.value,
        )
        return TaskResult(
            output=None,
            structured_output=None,
            check_results=[
                CheckResult(
                    passed=True,
                    message=(
                        "Decision point completed (stub)"
                    ),
                ),
            ],
        )

    async def _execute_agent_task(  # noqa: C901, PLR0912, PLR0915
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        if task.agent is None or task.task_prompt is None:
            msg = "Agent task requires agent and task_prompt"
            raise ValueError(msg)

        max_attempts = task.retry + 1
        check_results: list[CheckResult] = []
        prior_attempts: list[dict[str, Any]] = []

        for attempt in range(1, max_attempts + 1):
            attempt_id = (
                await self._trace_writer.create_task_attempt(
                    task_id, attempt,
                )
            )
            start_time = time.monotonic()
            self._task_start_times[task_id] = start_time

            if (
                attempt > 1
                and task.pre_retry is not None
            ):
                try:
                    await self._call_callback(
                        task.pre_retry,
                        self._make_task_context(
                            task, task_id, parent_task_id,
                            attempt, prior_attempts,
                            variables,
                        ),
                    )
                except Exception:  # noqa: BLE001
                    self._task_states[task_id] = (
                        TaskState.ESCALATED
                    )
                    await self._trace_writer.transition_task(
                        task_id,
                        TaskState.ESCALATED.value,
                    )
                    return TaskResult(
                        output=None,
                        structured_output=None,
                        check_results=[
                            CheckResult(
                                passed=False,
                                message="pre_retry aborted",
                            ),
                        ],
                    )

            session, session_id = (
                self._create_agent_session(
                    task, task_id, attempt,
                )
            )
            self._task_sessions[task_id] = session
            self._session_mutations[session_id] = False

            prompt = self._resolve_prompt(
                task.task_prompt, variables,
            )
            prompt = (
                f"Your task ID is {task_id}."
                " Call start_task first.\n\n"
                f"{prompt}"
            )

            # Layer 2: Runtime system context
            # Constraints
            if self._active_constraints:
                prompt += "\n\n## Active Constraints"
                for text, tier in (
                    self._active_constraints
                ):
                    prompt += f"\n- {text} ({tier})"

            # Notepad content
            if self._notepad_entries:
                prompt += (
                    f"\n\n{format_notepad(self._notepad_entries)}"
                )

            # Prior failure context
            if (
                attempt > 1
                and task.retry_inject_failure
                and prior_attempts
            ):
                prompt += (
                    "\n\n## Prior Failure Context"
                )
                for pa in prior_attempts:
                    prompt += (
                        f"\nPrior attempt {pa['attempt']}"
                        f" failed: {pa['error']}"
                    )

            snap_in = session.total_input_tokens
            snap_out = session.total_output_tokens
            snap_reason = (
                session.total_reasoning_tokens
            )
            snap_cache_r = (
                session.total_cache_read_tokens
            )
            snap_cache_w = (
                session.total_cache_write_tokens
            )

            try:
                if task.timeout is not None:
                    await asyncio.wait_for(
                        self._run_session(
                            session,
                            prompt,
                            session_id,
                            task_id,
                        ),
                        timeout=float(task.timeout),
                    )
                else:
                    await self._run_session(
                        session,
                        prompt,
                        session_id,
                        task_id,
                    )
            except TimeoutError:
                await self._fail_attempt_timeout(
                    attempt_id, session, task_id,
                )
                return TaskResult(
                    output=None,
                    structured_output=None,
                    check_results=[
                        CheckResult(
                            passed=False,
                            message="Task timed out",
                        ),
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                category = classify_error(exc)
                await self._trace_writer.write_event(
                    run_id=self._run_id,
                    event_type="task_error",
                    data={
                        "task_id": str(task_id),
                        "error": str(exc),
                        "error_type": (
                            type(exc).__name__
                        ),
                        "category": category.value,
                    },
                    task_id=task_id,
                )
                await self._complete_attempt(
                    attempt_id, session, "", False,
                    task_id=task_id,
                )
                check_results = [CheckResult(
                    passed=False,
                    message=(
                        f"[{category.value}] {exc}"
                    ),
                )]
                prior_attempts.append({
                    "attempt": attempt,
                    "error": (
                        f"[{category.value}] {exc}"
                    ),
                })
                if attempt < max_attempts:
                    self._task_states[task_id] = (
                        TaskState.CREATED
                    )
                    continue
                # Fall through to escalation
                break

            _ = time.monotonic() - start_time
            self._accumulate_cost(
                task_id, task,
                Usage(
                    input_tokens=(
                        session.total_input_tokens
                        - snap_in
                    ),
                    output_tokens=(
                        session.total_output_tokens
                        - snap_out
                    ),
                    reasoning_tokens=(
                        session.total_reasoning_tokens
                        - snap_reason
                    ),
                    cache_read_tokens=(
                        session.total_cache_read_tokens
                        - snap_cache_r
                    ),
                    cache_write_tokens=(
                        session.total_cache_write_tokens
                        - snap_cache_w
                    ),
                ),
            )

            state = self._task_states[task_id]

            if state == TaskState.COMPLETED:
                outputs = self._get_scoped_outputs(
                    self._task_parents.get(task_id),
                )
                result_text = outputs.get(
                    task.name,
                )

                # Validate structured output if schema
                # is defined
                if task.output_schema is not None:
                    validation = (
                        self._validate_output_schema(
                            result_text,
                            task.output_schema,
                        )
                    )
                    if not validation.passed:
                        await self._complete_attempt(
                            attempt_id, session,
                            "", False,
                            task_id=task_id,
                        )
                        check_results = [validation]
                        prior_attempts.append({
                            "attempt": attempt,
                            "error": (
                                "Output validation:"
                                f" {validation.message}"
                            ),
                        })
                        if attempt < max_attempts:
                            self._task_states[task_id] = (
                                TaskState.CREATED
                            )
                            continue
                        # Fall through to escalation
                        break

                await self._complete_attempt(
                    attempt_id, session,
                    result_text or "", True,
                    task_id=task_id,
                )
                return TaskResult(
                    output=result_text,
                    structured_output=None,
                    check_results=[
                        CheckResult(
                            passed=True,
                            message="Task completed",
                        ),
                    ],
                )

            if state == TaskState.POSTCHECK_FAILED:
                await self._complete_attempt(
                    attempt_id, session, "", False,
                    task_id=task_id,
                )
                check_results = [
                    CheckResult(
                        passed=False,
                        message="Postchecks failed",
                    ),
                ]
                prior_attempts.append({
                    "attempt": attempt,
                    "error": "Postchecks failed",
                })
                if attempt < max_attempts:
                    self._task_states[task_id] = (
                        TaskState.CREATED
                    )
                    continue

            elif state == TaskState.PRECHECK_FAILED:
                await self._complete_attempt(
                    attempt_id, session, "", False,
                    task_id=task_id,
                )
                check_results = [
                    CheckResult(
                        passed=False,
                        message="Prechecks failed",
                    ),
                ]
                prior_attempts.append({
                    "attempt": attempt,
                    "error": "Prechecks failed",
                })
                if attempt < max_attempts:
                    self._task_states[task_id] = (
                        TaskState.CREATED
                    )
                    continue

            else:
                await self._complete_attempt(
                    attempt_id, session, "", False,
                    task_id=task_id,
                )
                prior_attempts.append({
                    "attempt": attempt,
                    "error": (
                        f"Session ended in state {state}"
                    ),
                })
                if attempt < max_attempts:
                    self._task_states[task_id] = (
                        TaskState.CREATED
                    )
                    continue

        escalation = EscalationPayload(
            task_name=task.name,
            task_id=task_id,
            agent_name=task.agent,
            attempts=max_attempts,
            failed_checks=check_results,
            agent_summary="Retries exhausted",
            context=self._make_task_context(
                task, task_id, parent_task_id,
                max_attempts, prior_attempts,
                variables,
            ),
        )
        self._task_states[task_id] = TaskState.ESCALATED
        self._file_lock_registry.release(task_id)
        await self._trace_writer.transition_task(
            task_id, TaskState.ESCALATED.value,
        )
        return TaskResult(
            output=None,
            structured_output={
                "escalation": {
                    "task_name": escalation.task_name,
                    "attempts": escalation.attempts,
                    "agent_name": escalation.agent_name,
                },
            },
            check_results=check_results,
        )

    def _make_task_context(  # noqa: PLR0913
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        attempt: int,
        prior_attempts: list[dict[str, Any]],
        variables: dict[str, Any] | None,
    ) -> TaskContext:
        # Compute nesting depth by walking parent chain
        depth = 0
        current = task_id
        while self._task_parents.get(current) is not None:
            depth += 1
            current = self._task_parents[current]  # type: ignore[assignment]

        # Convert prior_attempts dicts to AttemptSummary
        summaries: list[AttemptSummary] | None = None
        if prior_attempts:
            summaries = [
                AttemptSummary(
                    attempt=pa["attempt"],
                    output=pa.get("error"),
                    check_results=[],
                    duration_seconds=0.0,
                )
                for pa in prior_attempts
            ]

        return TaskContext(
            variables=variables or {},
            run_id=self._run_id,
            task_name=task.name,
            task_id=task_id,
            attempt=attempt,
            prior_attempts=summaries,
            notepad_content=format_notepad(
                self._notepad_entries,
            ),
            parent_task_id=parent_task_id,
            nesting_depth=depth,
        )

    def _resolve_model_key(
        self, task: TaskSpec,
    ) -> str | None:
        if task.agent is None:
            return None
        agent_def = self._agents.get(task.agent)
        if agent_def is None:
            return None
        category_str = task.category or agent_def.category
        return self._categories.get(category_str)

    def _accumulate_cost(
        self,
        task_id: UUID,
        task: TaskSpec,
        usage: Usage,
    ) -> None:
        model_key = self._resolve_model_key(task)
        if model_key is None:
            return
        cost = compute_cost_usd(model_key, usage)
        self._task_costs[task_id] += cost

    def _create_agent_session(
        self,
        task: TaskSpec,
        task_id: UUID,
        attempt: int,
    ) -> tuple[Session, str]:
        if task.agent is None:
            msg = "Cannot create session without agent"
            raise ValueError(msg)

        agent_def = self._agents.get(task.agent)
        if agent_def is None:
            msg = f"Agent '{task.agent}' not found"
            raise ValueError(msg)

        category_str = task.category or agent_def.category
        resolved = self._categories.get(category_str)
        if resolved is None:
            msg = f"Category '{category_str}' not found"
            raise ValueError(msg)

        provider_name, model = resolved.split("/", 1)
        transport = self._transport_registry.get(
            provider_name,
        )
        if transport is None:
            msg = (
                "Transport for provider"
                f" '{provider_name}' not found"
            )
            raise ValueError(msg)

        session_id = f"session-{task_id}-{attempt}"
        tools = self._build_lifecycle_tools(
            task_id, session_id,
        )

        previous_session_id: str | None = None
        if (
            attempt > 1
            and task.retry_resume
            and task_id in self._task_sessions
        ):
            prev = self._task_sessions[task_id]
            previous_session_id = prev.session_id

        session = create_session(
            transport=transport,
            model=model,
            system_prompt=agent_def.prompt,
            tools=tools,
            trace_writer=self._trace_writer,
            run_id=self._run_id,
            session_id=previous_session_id,
        )
        return session, session_id

    def _build_lifecycle_tools(
        self,
        task_id: UUID,
        session_id: str,
    ) -> list[Tool]:
        async def start_task_execute(
            params: dict[str, Any],  # noqa: ARG001
        ) -> str:
            return await self.handle_start_task(
                session_id, task_id,
            )

        async def end_task_execute(
            params: dict[str, Any],
        ) -> str:
            message = params.get("message", "")
            return await self.handle_end_task(
                session_id, message,
            )

        raw_tools = [
            Tool(
                name="start_task",
                description=(
                    "Start the assigned task."
                    " Must be called before any work."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                execute=start_task_execute,
            ),
            Tool(
                name="end_task",
                description=(
                    "Signal task completion."
                    " Triggers postchecks."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": (
                                "Commit message for"
                                " auto-commit"
                            ),
                        },
                    },
                    "required": ["message"],
                    "additionalProperties": False,
                },
                execute=end_task_execute,
            ),
        ]

        # Wrap with pipeline: active task check,
        # secret substitution, mutation tracking
        return wrap_tools_for_session(
            tools=raw_tools,
            scheduler_check=self.check_active_task,
            secret_registry=None,
            trace_callback=None,
            session_id=session_id,
            mutation_tracker=self._session_mutations,
        )

    @staticmethod
    def _resolve_prompt(
        template: str,
        variables: dict[str, Any] | None,
    ) -> str:
        prompt = template
        if variables:
            for k, v in variables.items():
                prompt = prompt.replace(
                    f"{{{k}}}", str(v),
                )
        return prompt

    async def _run_session(
        self,
        session: Session,
        prompt: str,
        session_id: str,  # noqa: ARG002
        task_id: UUID,  # noqa: ARG002
    ) -> str:
        result_text = ""
        async for event in session.send(prompt):
            if isinstance(event, Result):
                result_text = event.text
        return result_text

    async def _fail_attempt_timeout(
        self,
        attempt_id: UUID,
        session: Session,
        task_id: UUID,
    ) -> None:
        duration = time.monotonic() - self._task_start_times.get(
            task_id, time.monotonic(),
        )
        await self._trace_writer.fail_task_attempt(
            attempt_id=attempt_id,
            error="Task timed out",
            session_id=None,
            input_tokens=session.total_input_tokens,
            output_tokens=session.total_output_tokens,
            reasoning_tokens=(
                session.total_reasoning_tokens
            ),
            cache_read_tokens=(
                session.total_cache_read_tokens
            ),
            cache_write_tokens=(
                session.total_cache_write_tokens
            ),
            cost_usd=self._task_costs.get(
                task_id, Decimal(0),
            ),
            duration_seconds=duration,
        )
        self._task_states[task_id] = TaskState.CANCELLED
        await self._trace_writer.transition_task(
            task_id, TaskState.CANCELLED.value,
        )

    async def _complete_attempt(
        self,
        attempt_id: UUID,
        session: Session,
        result_text: str,
        passed: bool,
        task_id: UUID,
    ) -> None:
        duration = time.monotonic() - self._task_start_times.get(
            task_id, time.monotonic(),
        )
        await self._trace_writer.complete_task_attempt(
            attempt_id=attempt_id,
            agent_output=result_text,
            structured_output=None,
            check_result=None,
            check_verdict="pass" if passed else "fail",
            session_id=None,
            input_tokens=session.total_input_tokens,
            output_tokens=session.total_output_tokens,
            reasoning_tokens=(
                session.total_reasoning_tokens
            ),
            cache_read_tokens=(
                session.total_cache_read_tokens
            ),
            cache_write_tokens=(
                session.total_cache_write_tokens
            ),
            cost_usd=self._task_costs.get(
                task_id, Decimal(0),
            ),
            duration_seconds=duration,
        )

    def _complete_task(  # noqa: PLR0913
        self,
        task_id: UUID,
        task_name: str,
        output: str | None,
        *,
        structured: dict[str, Any] | None = None,
        duration: float = 0.0,
        retries: int = 0,
    ) -> None:
        self._task_states[task_id] = TaskState.COMPLETED
        self._file_lock_registry.release(task_id)
        parent_id = self._task_parents.get(task_id)
        outputs = self._get_scoped_outputs(parent_id)
        structured_outputs = self._get_scoped_structured(
            parent_id,
        )
        results_meta = self._get_scoped_results_meta(
            parent_id,
        )
        outputs[task_name] = output
        structured_outputs[task_name] = structured
        results_meta[task_name] = {
            "passed": True,
            "duration_seconds": duration,
            "retries_used": retries,
        }

    async def _run_postchecks(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> list[CheckResult]:
        if not task.postchecks:
            return [
                CheckResult(
                    passed=True,
                    message="No postchecks defined",
                ),
            ]
        message = self._pending_end_task_message.get(
            task_id, "",
        )
        ctx = CheckContext(
            variables={},
            agent_output=message,
            run_id=self._run_id,
            session_id=None,
            task_name=task.name,
            task_id=task_id,
            attempt=1,
            parent_task_id=self._task_parents.get(
                task_id,
            ),
        )
        return await run_checks(
            task.postchecks, ctx, "postcheck", self,
        )

    async def _run_prechecks(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> list[CheckResult]:
        if not task.prechecks:
            return [
                CheckResult(
                    passed=True,
                    message="No prechecks defined",
                ),
            ]
        ctx = CheckContext(
            variables={},
            agent_output=None,
            run_id=self._run_id,
            session_id=None,
            task_name=task.name,
            task_id=task_id,
            attempt=1,
            parent_task_id=self._task_parents.get(
                task_id,
            ),
        )
        return await run_checks(
            task.prechecks, ctx, "precheck", self,
        )

    async def _run_mechanical_constraints(
        self,
        task_id: UUID,
    ) -> list[CheckResult]:
        """Run active mechanical constraints.

        Cheap constraints run after every task.
        Expensive constraints (tests_pass, lint_clean)
        run only when the completing task has subtasks
        (workflow completion).
        """
        results: list[CheckResult] = []
        has_subtasks = bool(
            self._task_children.get(task_id),
        )

        for _text, kind_str in self._mechanical_constraints:
            try:
                kind = ConstraintKind(kind_str)
            except ValueError:
                results.append(CheckResult(
                    passed=False,
                    message=(
                        "Unknown constraint kind:"
                        f" {kind_str}"
                    ),
                ))
                continue

            # Skip expensive constraints unless this
            # is workflow completion
            if (
                kind in EXPENSIVE_CONSTRAINTS
                and not has_subtasks
            ):
                continue

            result = await self._check_constraint(
                kind, task_id,
            )
            results.append(result)

        return results

    async def _check_constraint(
        self,
        kind: ConstraintKind,
        task_id: UUID,  # noqa: ARG002
    ) -> CheckResult:
        """Check a single mechanical constraint.

        Stubs for now -- each ConstraintKind will have
        its own checker once implemented.
        """
        return CheckResult(
            passed=True,
            message=(
                f"Constraint {kind.value} passed (stub)"
            ),
        )

    def _validate_output_schema(
        self,
        output: str | None,
        schema_str: str,
    ) -> CheckResult:
        """Validate agent output against a JSON schema."""
        if output is None:
            return CheckResult(
                passed=False,
                message=(
                    "No output to validate against schema"
                ),
            )

        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as e:
            return CheckResult(
                passed=False,
                message=f"Output is not valid JSON: {e}",
            )

        try:
            schema = json.loads(schema_str)
        except json.JSONDecodeError as e:
            return CheckResult(
                passed=False,
                message=(
                    "Output schema is not valid JSON:"
                    f" {e}"
                ),
            )

        try:
            jsonschema.validate(parsed, schema)
        except jsonschema.ValidationError as e:
            return CheckResult(
                passed=False,
                message=(
                    "Output validation failed:"
                    f" {e.message}"
                ),
            )

        return CheckResult(
            passed=True, message="Output validated",
        )

    async def _execute_function_task(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        if task.callable is None:
            msg = "Function task requires callable"
            raise ValueError(msg)

        attempt_id = (
            await self._trace_writer.create_task_attempt(
                task_id, 1,
            )
        )
        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        parts = task.callable.split(":")
        if len(parts) != 2:  # noqa: PLR2004
            msg = (
                f"Invalid callable path: {task.callable!r}"
                " (expected 'module.path:function')"
            )
            raise ValueError(msg)
        module_path, func_name = parts
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)

        # Compute nesting depth
        depth = 0
        current = task_id
        while self._task_parents.get(current) is not None:
            depth += 1
            current = self._task_parents[current]  # type: ignore[assignment]

        context = TaskContext(
            variables=variables or {},
            run_id=self._run_id,
            task_name=task.name,
            task_id=task_id,
            attempt=1,
            prior_attempts=None,
            notepad_content=format_notepad(
                self._notepad_entries,
            ),
            parent_task_id=parent_task_id,
            nesting_depth=depth,
        )

        result: TaskResult = await func(context)

        await self._trace_writer.complete_task_attempt(
            attempt_id=attempt_id,
            agent_output=result.output or "",
            structured_output=result.structured_output,
            check_result=None,
            check_verdict="pass",
            session_id=None,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=Decimal(0),
            duration_seconds=0.0,
        )

        self._complete_task(
            task_id,
            task.name,
            result.output,
            structured=result.structured_output,
        )
        await self._trace_writer.transition_task(
            task_id, TaskState.COMPLETED.value,
        )
        return result

    async def _execute_composite_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult:
        if task.subtasks is None:
            msg = "Composite task requires subtasks"
            raise ValueError(msg)

        # Run prechecks before activating
        self._task_states[task_id] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.PRECHECKING.value,
        )
        precheck_results = await self._run_prechecks(
            task, task_id,
        )
        if not all(
            cr.passed for cr in precheck_results
        ):
            self._task_states[task_id] = (
                TaskState.PRECHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_id,
                TaskState.PRECHECK_FAILED.value,
            )
            return TaskResult(
                output=None,
                structured_output=None,
                check_results=precheck_results,
            )

        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        for sub in task.subtasks:
            t = asyncio.create_task(
                self.execute_task(sub, task_id),
            )
            self._running_tasks.add(t)
            t.add_done_callback(self._running_tasks.discard)
            await t

        # Run postchecks before completing
        self._task_states[task_id] = (
            TaskState.POSTCHECKING
        )
        await self._trace_writer.transition_task(
            task_id, TaskState.POSTCHECKING.value,
        )
        postcheck_results = await self._run_postchecks(
            task, task_id,
        )
        if not all(
            cr.passed for cr in postcheck_results
        ):
            self._task_states[task_id] = (
                TaskState.POSTCHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_id,
                TaskState.POSTCHECK_FAILED.value,
            )
            return TaskResult(
                output=None,
                structured_output=None,
                check_results=postcheck_results,
            )

        self._task_states[task_id] = TaskState.COMPLETED
        await self._trace_writer.transition_task(
            task_id, TaskState.COMPLETED.value,
        )
        return TaskResult(
            output=None,
            structured_output=None,
            check_results=postcheck_results,
        )

    async def _execute_wait_for_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult:
        if task.wait_for is None:
            msg = "Wait-for task requires wait_for"
            raise ValueError(msg)

        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        seconds = (
            float(task.timeout) if task.timeout else 60.0
        )
        payload = await self._event_registry.wait_for(
            task.wait_for, deadline_seconds=seconds,
        )

        if payload is not None:
            self._task_states[task_id] = TaskState.COMPLETED
            await self._trace_writer.transition_task(
                task_id, TaskState.COMPLETED.value,
            )
            return TaskResult(
                output=str(payload),
                structured_output=dict(payload),
                check_results=[
                    CheckResult(
                        passed=True,
                        message="Event received",
                    ),
                ],
            )

        self._task_states[task_id] = TaskState.CANCELLED
        await self._trace_writer.transition_task(
            task_id, TaskState.CANCELLED.value,
        )
        return TaskResult(
            output=None,
            structured_output=None,
            check_results=[
                CheckResult(
                    passed=False,
                    message="Wait timed out",
                ),
            ],
        )

    async def _execute_for_each(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,  # noqa: ARG002
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        if (
            task.for_each is None
            or task.max_concurrency is None
        ):
            msg = (
                "for_each task requires for_each"
                " and max_concurrency"
            )
            raise ValueError(msg)

        items = (variables or {}).get(task.for_each, [])
        if not isinstance(items, list):
            msg = (
                f"for_each variable '{task.for_each}'"
                " must resolve to a list"
            )
            raise TypeError(msg)

        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        semaphore = asyncio.Semaphore(task.max_concurrency)
        results: list[TaskResult | None] = (
            [None] * len(items)
        )
        abort = False

        async def run_iteration(
            idx: int, item: object,
        ) -> None:
            nonlocal abort
            if abort:
                return
            async with semaphore:
                if abort:
                    return
                iter_vars = dict(variables or {})
                iter_vars["item"] = item

                iter_task = task.model_copy(
                    update={
                        "name": f"{task.name}_iter_{idx}",
                        "for_each": None,
                        "for_each_abort_on_failure": None,
                        "max_concurrency": None,
                    },
                )
                try:
                    result = await self.execute_task(
                        iter_task,
                        task_id,
                        variables=iter_vars,
                    )
                    results[idx] = result
                    if (
                        not all(
                            cr.passed
                            for cr in result.check_results
                        )
                        and task.for_each_abort_on_failure
                    ):
                        abort = True
                except Exception:  # noqa: BLE001
                    if task.for_each_abort_on_failure:
                        abort = True

        async_tasks = [
            asyncio.create_task(
                run_iteration(i, item),
            )
            for i, item in enumerate(items)
        ]
        for t in async_tasks:
            self._running_tasks.add(t)
            t.add_done_callback(self._running_tasks.discard)
        await asyncio.gather(*async_tasks)

        if abort:
            self._task_states[task_id] = (
                TaskState.POSTCHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_id,
                TaskState.POSTCHECK_FAILED.value,
            )
        else:
            self._complete_task(
                task_id, task.name,
                str([
                    r.output if r is not None else None
                    for r in results
                ]),
            )
            await self._trace_writer.transition_task(
                task_id, TaskState.COMPLETED.value,
            )

        outputs = [
            r.output if r is not None else None
            for r in results
        ]
        return TaskResult(
            output=str(outputs),
            structured_output={"iterations": outputs},
            check_results=[
                CheckResult(
                    passed=not abort,
                    message="for_each complete",
                ),
            ],
        )

    async def handle_start_task(
        self, session_id: str, task_id: UUID,
    ) -> str:
        if task_id not in self._task_states:
            msg = f"Task {task_id} does not exist"
            raise ToolError(msg)

        state = self._task_states[task_id]
        if state != TaskState.CREATED:
            msg = (
                f"Task {task_id} is in state '{state}',"
                " expected 'created'"
            )
            raise ToolError(msg)

        self._task_states[task_id] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.PRECHECKING.value,
        )

        task = self._task_specs[task_id]
        precheck_results = await self._run_prechecks(
            task, task_id,
        )
        if not all(
            cr.passed for cr in precheck_results
        ):
            self._task_states[task_id] = (
                TaskState.PRECHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_id, TaskState.PRECHECK_FAILED.value,
            )
            failed = [
                cr.message
                for cr in precheck_results
                if not cr.passed
            ]
            return (
                f"Prechecks failed: {'; '.join(failed)}"
            )

        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )
        self._active_tasks[session_id] = task_id
        return f"Task {task_id} is now active"

    async def handle_end_task(
        self,
        session_id: str,
        message: str,
    ) -> str:
        task_id = self.check_active_task(session_id)
        task = self._task_specs[task_id]

        children = self._task_children.get(task_id, [])
        incomplete = [
            cid
            for cid in children
            if self._task_states.get(cid)
            != TaskState.COMPLETED
        ]
        if incomplete:
            n = len(incomplete)
            msg = (
                f"Cannot end task:"
                f" {n} subtask(s) not complete"
            )
            raise ToolError(msg)

        self._task_states[task_id] = TaskState.POSTCHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.POSTCHECKING.value,
        )

        self._pending_end_task_message[task_id] = message
        check_results = await self._run_postchecks(
            task, task_id,
        )
        all_passed = all(
            cr.passed for cr in check_results
        )

        if all_passed:
            # Run mechanical constraints
            constraint_results = (
                await self._run_mechanical_constraints(
                    task_id,
                )
            )
            constraint_passed = all(
                cr.passed for cr in constraint_results
            )
            if not constraint_passed:
                self._task_states[task_id] = (
                    TaskState.POSTCHECK_FAILED
                )
                await self._trace_writer.transition_task(
                    task_id,
                    TaskState.POSTCHECK_FAILED.value,
                )
                failed = [
                    cr.message
                    for cr in constraint_results
                    if not cr.passed
                ]
                return (
                    "Constraint check failed:"
                    f" {'; '.join(failed)}"
                )

            await self._auto_commit(session_id, message)
            self._complete_task(
                task_id, task.name, message,
            )
            await self._trace_writer.transition_task(
                task_id, TaskState.COMPLETED.value,
            )
            del self._active_tasks[session_id]
            if task.on_success is not None:
                try:
                    parent_id = self._task_parents.get(
                        task_id,
                    )
                    await self._call_callback(
                        task.on_success,
                        self._make_task_context(
                            task, task_id, parent_id,
                            1, [], None,
                        ),
                    )
                except Exception:  # noqa: BLE001, S110
                    pass
            return f"Task {task_id} completed"

        self._task_states[task_id] = (
            TaskState.POSTCHECK_FAILED
        )
        await self._trace_writer.transition_task(
            task_id, TaskState.POSTCHECK_FAILED.value,
        )
        failed = [
            cr.message
            for cr in check_results
            if not cr.passed
        ]
        return (
            f"Postchecks failed: {'; '.join(failed)}"
        )

    async def _auto_commit(
        self,
        session_id: str,
        message: str,
    ) -> None:
        mutations_detected = self._session_mutations.get(
            session_id, False,
        )
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        dirty_files = stdout.decode().strip()

        if mutations_detected:
            if dirty_files:
                changed = []
                for line in dirty_files.splitlines():
                    # porcelain format: XY filename
                    # or XY old -> new
                    parts = line.strip().split(
                        maxsplit=1,
                    )
                    if len(parts) >= 2:  # noqa: PLR2004
                        fname = parts[1]
                        if " -> " in fname:
                            fname = fname.split(
                                " -> ",
                            )[1]
                        changed.append(fname)
                if changed:
                    file_args = ["--", *changed]
                    proc = (
                        await asyncio.create_subprocess_exec(
                            "safegit", "commit",
                            "-m", message,
                            *file_args,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                    )
                    await proc.communicate()
            else:
                _logger.warning(
                    "Mutation tracker detected changes"
                    " for session %s but git working"
                    " tree is clean",
                    session_id,
                )
        elif dirty_files:
            _logger.warning(
                "Git working tree has changes but"
                " mutation tracker reports none"
                " for session %s",
                session_id,
            )

    async def handle_create_task(
        self,
        session_id: str,
        params: CreateTaskParams,
    ) -> str:
        parent_id = self.check_active_task(session_id)

        task_id = await self._trace_writer.create_task(
            run_id=self._run_id,
            parent_task_id=parent_id,
            name=params.name,
            task_type="agent",
        )
        self._task_states[task_id] = TaskState.CREATED
        spec = TaskSpec(
            name=params.name,
            agent=params.agent,
            task_prompt=params.task_prompt,
            prechecks=list(params.prechecks),
            postchecks=list(params.postchecks),
            timeout=params.timeout,
            context_refinement=params.context_refinement,
            budget=params.budget,
            write_paths=params.write_paths,
            category=params.category,
            retry=params.retry,
            retry_resume=params.retry_resume,
            retry_inject_failure=(
                params.retry_inject_failure
            ),
        )
        self._task_specs[task_id] = spec
        self._task_parents[task_id] = parent_id
        self._task_children.setdefault(
            parent_id, [],
        ).append(task_id)
        self._task_costs[task_id] = Decimal(0)

        # Claim write paths in file lock registry
        if params.write_paths:
            try:
                self._file_lock_registry.claim(
                    task_id, params.write_paths,
                )
            except ValueError as e:
                raise ToolError(str(e)) from e

        # Re-analyze parent's subtree for advisories
        children = self._task_children.get(
            parent_id, [],
        )
        advisories = self._analyze_structural_advisories(
            children,
        )
        if advisories:
            self._pending_advisories.extend(advisories)

        return str(task_id)

    async def handle_create_workflow(
        self,
        session_id: str,
        params: CreateWorkflowParams,
    ) -> str:
        parent_id = self.check_active_task(session_id)

        workflow_id = await self._trace_writer.create_task(
            run_id=self._run_id,
            parent_task_id=parent_id,
            name=params.name,
            task_type="workflow",
        )
        spec = TaskSpec(
            name=params.name,
            subtasks=[],
        )
        self._task_states[workflow_id] = TaskState.CREATED
        self._task_specs[workflow_id] = spec
        self._task_parents[workflow_id] = parent_id
        self._task_children.setdefault(
            parent_id, [],
        ).append(workflow_id)
        self._task_children[workflow_id] = []
        self._task_costs[workflow_id] = Decimal(0)
        return str(workflow_id)

    async def handle_create_wait_for(
        self,
        session_id: str,
        params: CreateWaitForParams,
    ) -> str:
        parent_id = self.check_active_task(session_id)

        task_id = await self._trace_writer.create_task(
            run_id=self._run_id,
            parent_task_id=parent_id,
            name=params.name,
            task_type="wait_for",
        )
        self._task_states[task_id] = TaskState.CREATED
        spec = TaskSpec(
            name=params.name,
            wait_for=params.event_name,
            timeout=params.timeout,
        )
        self._task_specs[task_id] = spec
        self._task_parents[task_id] = parent_id
        self._task_children.setdefault(
            parent_id, [],
        ).append(task_id)
        self._task_costs[task_id] = Decimal(0)
        self._event_registry.register(
            params.event_name, task_id,
        )
        return str(task_id)

    def check_active_task(self, session_id: str) -> UUID:
        task_id = self._active_tasks.get(session_id)
        if task_id is None:
            msg = (
                "No active task for session"
                f" '{session_id}'"
            )
            raise ToolError(msg)
        return task_id

    async def abort(self) -> None:
        for atask in list(self._running_tasks):
            atask.cancel()
        for task_id, state in list(
            self._task_states.items(),
        ):
            if state in _ACTIVE_STATES:
                self._task_states[task_id] = (
                    TaskState.CANCELLED
                )
                self._file_lock_registry.release(task_id)
                await self._trace_writer.transition_task(
                    task_id, TaskState.CANCELLED.value,
                )

    async def pause(self) -> None:
        self._paused.clear()
        for atask in list(self._running_tasks):
            atask.cancel()
        await self._trace_writer.transition_run(
            self._run_id, "paused",
        )

    async def resume(self) -> None:
        self._paused.set()
        await self._trace_writer.transition_run(
            self._run_id, "running",
        )

    @property
    def is_paused(self) -> bool:
        return not self._paused.is_set()

    def _analyze_structural_advisories(
        self,
        task_ids: list[UUID],
    ) -> list[dict[str, Any]]:
        """Analyze a set of tasks for structural improvements.

        Detects read-only tasks that could be front-loaded
        for earlier execution. Advisories are stored for the
        Overseer to consume in Phase 3.
        """
        advisories: list[dict[str, Any]] = []

        if not task_ids:
            return advisories

        # Detect read-only agent tasks
        write_tools = {
            "write", "edit", "delete", "move",
            "copy", "mkdir", "set_executable",
        }
        read_only_names: list[str] = []
        for task_id in task_ids:
            spec = self._task_specs.get(task_id)
            if spec is None or spec.agent is None:
                continue
            agent_def = self._agents.get(spec.agent)
            if agent_def is None:
                continue
            has_write = bool(
                set(agent_def.allow) & write_tools,
            )
            if not has_write:
                read_only_names.append(spec.name)

        if read_only_names:
            advisories.append({
                "type": "front_load_read_only",
                "message": (
                    f"Tasks {read_only_names} are read-only"
                    " and could be front-loaded for earlier"
                    " execution"
                ),
                "task_names": read_only_names,
            })

        return advisories

    async def _send_overseer_event(
        self, event: OverseerEvent,
    ) -> None:
        """Send an event to the Overseer with a
        verify-then-accept retry loop."""
        if self._overseer_interface is None:
            return

        event_type = type(event).__name__

        # Check degraded mode first
        if self._overseer_interface.is_degraded(
            event_type,
        ):
            from orxt.scheduler._overseer import (  # noqa: PLC0415
                _DEFAULT_FALLBACK,
                FALLBACK_BEHAVIORS,
            )
            behavior = FALLBACK_BEHAVIORS.get(
                event_type, _DEFAULT_FALLBACK,
            )
            _logger.warning(
                "Overseer degraded for %s, using"
                " fallback: %s",
                event_type,
                behavior,
            )
            return

        max_attempts = 3
        errors: list[str] = []

        for attempt in range(max_attempts):
            if attempt == 0:
                await (
                    self._overseer_interface.send_event(
                        event,
                    )
                )
            else:
                correction = (
                    "Your previous response had"
                    " issues:\n"
                    + "\n".join(errors)
                )
                await (
                    self._overseer_interface
                    .send_correction(
                        correction,
                    )
                )

            errors = (
                await (
                    self._overseer_interface
                    .verify_actions(
                        event_type,
                    )
                )
            )
            if not errors:
                break
        else:
            # All attempts failed -- log
            _logger.warning(
                "Overseer failed verification"
                " %d times for %s: %s",
                max_attempts,
                event_type,
                errors,
            )

        # Check session handoff after event
        await self._check_session_handoff()

    async def _write_coherence_summary(self) -> None:
        """Ask the Overseer for a coherence summary
        and persist it."""
        if self._overseer_interface is None:
            return

        parts: list[str] = []

        # The OverseerInterface protocol doesn't
        # expose session directly, but
        # OverseerAdapter does. Use duck typing.
        adapter = self._overseer_interface
        if not hasattr(adapter, "session"):
            return

        async for ev in adapter.session.send(  # type: ignore[union-attr]
            "The run is complete. Review the work "
            "against the original intent and "
            "produce a coherence summary.",
        ):
            if isinstance(ev, Result):
                parts.append(ev.text)  # noqa: PERF401

        summary = "".join(parts)
        if summary:
            await self._trace_writer.write_coherence_summary(
                self._run_id, summary,
            )

    async def _check_session_handoff(self) -> None:
        """Check if the Overseer session needs
        handoff due to token usage."""
        if self._overseer_interface is None:
            return
        if not hasattr(
            self._overseer_interface, "session",
        ):
            return
        if (
            self._handoff_checker is None
            or self._handoff_performer is None
        ):
            return

        adapter = self._overseer_interface
        session = adapter.session  # type: ignore[union-attr]
        needed = await self._handoff_checker(
            session, self._model_context_limit,
        )
        if needed:
            _logger.info(
                "Overseer session handoff"
                " triggered",
            )
            new_session = await self._handoff_performer(
                session,
                self._trace_writer,
                self._run_id,
            )
            adapter.update_session(new_session)  # type: ignore[union-attr]

    async def _send_pending_advisories(self) -> None:
        """Send stored structural advisories to the
        Overseer."""
        if self._overseer_interface is None:
            return
        import uuid as _uuid

        for advisory in self._pending_advisories:
            event = StructuralAdvisory(
                task_id=_uuid.UUID(int=0),
                observation=advisory["message"],
                suggestion=advisory.get(
                    "suggestion", advisory["message"],
                ),
            )
            await self._send_overseer_event(event)
        self._pending_advisories.clear()

    @staticmethod
    async def _call_callback(
        callable_path: str,
        context: TaskContext,
    ) -> None:
        parts = callable_path.split(":")
        if len(parts) != 2:  # noqa: PLR2004
            msg = (
                f"Invalid callable path:"
                f" {callable_path!r}"
                " (expected 'module.path:function')"
            )
            raise ValueError(msg)
        module_path, func_name = parts
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)
        await func(context)
