from __future__ import annotations

import asyncio
import logging
import re
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxtra.compose import CompositionEngine, resolve_variables

# Matches {variable_name} placeholders (same pattern as compose._variables)
_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _resolve_task_variables(
    template: str,
    variables: dict[str, Any] | None,
) -> str:
    """Resolve variables in a task prompt template.

    Wraps compose's strict resolve_variables with two adaptations
    for the scheduler's workflow context:

    1. Values are coerced to str (callers pass dict[str, Any]).
    2. Unused variables are filtered out rather than rejected,
       because the workflow executor accumulates all dependency
       outputs as variables and tasks use only a subset.

    Unresolved placeholders still raise ValueError (catches typos
    in task prompts).
    """
    if not variables:
        # No variables: still check for unresolved placeholders
        return resolve_variables(template, {})
    placeholders = set(_VAR_RE.findall(template))
    filtered = {
        k: str(v) for k, v in variables.items()
        if k in placeholders
    }
    return resolve_variables(template, filtered)
from orxtra.protocols import (
    CheckResult,
    EscalationPayload,
    TaskResult,
    TaskSpec,
    TaskState,
    Tool,
    ToolOutput,
)
from orxtra.scheduler._allow_resolver import resolve_allow_list
from orxtra.scheduler._prompt_providers import (
    ConstraintsProvider,
    FailureContextProvider,
    LessonsProvider,
    NotepadProvider,
    TaskPreambleProvider,
    TaskPromptProvider,
)
from orxtra.scheduler._prompt_templates import (
    render_template,
)
from orxtra.scheduler._tool_registry import (
    SYNTHETIC_ENTRIES,
    WRITE_TOOL_NAMES,
    ToolDeps,
)
from orxtra.session import Session, create_session
from orxtra.tool import (
    make_await_task_tool,
    make_consult_tool,
    make_create_task_tool,
    make_create_wait_for_tool,
    make_create_workflow_tool,
    make_end_task_tool,
    make_git_tool,
    make_load_tools_tool,
    make_start_task_tool,
    wrap_tool_with_pipeline,
    wrap_tools_for_session,
)
from orxtra.transport import Result, Usage

if TYPE_CHECKING:
    from orxtra.agent import Agent

from orxtra.scheduler._base import SchedulerBase

_logger = logging.getLogger("orxtra.scheduler")


async def _deferred_stub_execute(
    args: dict[str, Any],  # noqa: ARG001
) -> ToolOutput[Any]:
    """Placeholder execute for deferred tool stubs.

    If called, the LLM tried to use the tool before loading it.
    """
    from orxtra.protocols import ToolError  # noqa: PLC0415
    msg = (
        "This tool is deferred. "
        "Call load_tools first to load the full schema."
    )
    raise ToolError(msg)


class AgentExecutionMixin(SchedulerBase):
    """Mixin for agent and orchestrator task execution."""

    async def _execute_orchestrator_task(  # noqa: C901, PLR0915
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        """Execute an orchestrator task with multi-turn suspension support."""
        from orxtra.transport import SessionSuspended  # noqa: PLC0415

        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        session, session_id_str = await self._create_agent_session(
            task, task_id, 1,
        )
        # Register the orchestrator's session so
        # create_task/await_task can find the active task
        self._active_tasks[session_id_str] = task_id

        try:
            prompt = _resolve_task_variables(
                task.task_prompt or "",
                variables,
            )

            output_text = ""
            continuation = None

            async for event in session.send(prompt):
                if isinstance(event, SessionSuspended):
                    continuation = event.continuation
                    break
                if isinstance(event, Result):
                    output_text = event.text or ""

            while continuation is not None:
                child_task_id_str = self._pending_await.pop(
                    session_id_str, None,
                )
                if child_task_id_str is None:
                    break

                child_task_id = UUID(child_task_id_str)
                child_spec = self._task_specs.get(child_task_id)
                if child_spec is None:
                    break

                self._task_states[task_id] = TaskState.SUSPENDED
                await self._trace_writer.transition_task(
                    task_id,
                    TaskState.SUSPENDED.value,
                    "awaiting child task",
                )

                child_result = await self.execute_task(
                    child_spec, task_id,
                    task_id=child_task_id,
                )

                self._task_states[task_id] = TaskState.ACTIVE
                await self._trace_writer.transition_task(
                    task_id,
                    TaskState.ACTIVE.value,
                    "child task completed",
                )

                resume_msg = render_template(
                    "orchestrator_resume",
                    {
                        "child_task_id": child_task_id_str,
                        "child_result": (
                            child_result.output
                            or "no output"
                        ),
                    },
                )

                current_cont = continuation
                continuation = None
                async for ev in session.resume(
                    current_cont,
                    resume_msg,
                ):
                    if isinstance(ev, SessionSuspended):
                        continuation = ev.continuation
                        break
                    if isinstance(ev, Result):
                        output_text = ev.text or ""

            # Run postchecks if defined
            if task.postchecks:
                self._task_states[task_id] = TaskState.POSTCHECKING
                await self._trace_writer.transition_task(
                    task_id, TaskState.POSTCHECKING.value,
                )

                postcheck_results = await self._run_postchecks(
                    task, task_id,
                )
                if not all(cr.passed for cr in postcheck_results):
                    self._task_states[task_id] = (
                        TaskState.POSTCHECK_FAILED
                    )
                    await self._trace_writer.transition_task(
                        task_id,
                        TaskState.POSTCHECK_FAILED.value,
                    )
                    # Orchestrator session has ended, can't retry.
                    # Escalate immediately.
                    self._task_states[task_id] = (
                        TaskState.ESCALATED
                    )
                    await self._trace_writer.transition_task(
                        task_id,
                        TaskState.ESCALATED.value,
                    )

                    from orxtra.protocols import (  # noqa: PLC0415
                        TaskEscalated,
                    )

                    escalation = EscalationPayload(
                        task_name=task.name,
                        task_id=task_id,
                        agent_name=task.agent,
                        attempts=1,
                        failed_checks=[
                            cr for cr in postcheck_results
                            if not cr.passed
                        ],
                        agent_summary=(
                            "Orchestrator postchecks failed"
                        ),
                        context=self._make_task_context(
                            task, task_id, parent_task_id,
                            1, [], variables,
                        ),
                    )
                    await self._send_overseer_event(
                        TaskEscalated(
                            task_id=task_id,
                            task_name=task.name,
                            from_child_task_id=task_id,
                            payload=escalation,
                        ),
                    )

                    return TaskResult(
                        output=None,
                        structured_output=None,
                        check_results=postcheck_results,
                    )

                # Postchecks passed
                self._task_states[task_id] = TaskState.COMPLETED
                await self._trace_writer.transition_task(
                    task_id, TaskState.COMPLETED.value,
                )
                return TaskResult(
                    output=output_text,
                    structured_output=None,
                    check_results=postcheck_results,
                )

            # No postchecks: complete directly
            self._task_states[task_id] = TaskState.COMPLETED
            await self._trace_writer.transition_task(
                task_id, TaskState.COMPLETED.value,
            )

            return TaskResult(
                output=output_text,
                structured_output=None,
                check_results=[],
            )
        finally:
            await session.close()

    async def _execute_orchestrator_or_agent_task(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None,
    ) -> TaskResult:
        """Dispatch to orchestrator or agent task execution."""
        if task.orchestrator:
            return await self._execute_orchestrator_task(
                task, task_id, parent_task_id, variables,
            )
        return await self._execute_agent_task(
            task, task_id, parent_task_id, variables,
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
                await self._create_agent_session(
                    task, task_id, attempt,
                )
            )
            self._task_sessions[task_id] = session
            self._session_mutations[session_id] = set()

            await self._refresh_injection_data()

            prompt = await self._assemble_agent_prompt(
                task, task_id, variables, attempt,
                attempt_id, prior_attempts,
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

            # Resolve effective timeout: task overrides
            # agent default
            effective_timeout = task.timeout
            if effective_timeout is None and task.agent:
                agent_def_t = self._agents.get(
                    task.agent,
                )
                if agent_def_t is not None:
                    effective_timeout = (
                        agent_def_t.timeout
                    )

            try:
                if effective_timeout is not None:
                    await asyncio.wait_for(
                        self._run_session(
                            session,
                            prompt,
                            session_id,
                            task_id,
                        ),
                        timeout=float(effective_timeout),
                    )
                else:
                    await self._run_session(
                        session,
                        prompt,
                        session_id,
                        task_id,
                    )
            except TimeoutError:
                await session.close()
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
                await session.close()
                from orxtra.scheduler._executor import classify_error  # noqa: PLC0415
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

            # Drain pending sink tasks after successful session run
            await session.close()

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
            await self._send_budget_events(task_id)

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

        # Try parent agent first, fall back to Overseer
        if parent_task_id is not None:
            parent_session = self._task_sessions.get(parent_task_id)
        else:
            parent_session = None
        if (
            parent_session is not None
            and parent_task_id is not None
            and self._task_states.get(parent_task_id) == TaskState.ACTIVE
        ):
            failed_msgs = str(
                [
                    cr.message
                    for cr in check_results
                    if not cr.passed
                ],
            )
            escalation_msg = render_template(
                "escalation_to_parent",
                {
                    "task_name": task.name,
                    "max_attempts": str(max_attempts),
                    "failed_checks": failed_msgs,
                },
            )
            async for _ in parent_session.send(escalation_msg):
                pass
        else:
            from orxtra.protocols import (  # noqa: PLC0415
                TaskEscalated,
            )
            await self._send_overseer_event(
                TaskEscalated(
                    task_id=task_id,
                    task_name=task.name,
                    from_child_task_id=task_id,
                    payload=escalation,
                ),
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

    async def _run_session(
        self,
        session: Session,
        prompt: str,
        session_id: str,
        task_id: UUID,
    ) -> str:
        result_text = ""
        async for event in session.send(prompt):
            if isinstance(event, Result):
                result_text = event.text

        # Check agent context window usage after session run
        await self._check_agent_context(session, session_id, task_id)

        return result_text

    def _compute_context_usage(
        self, session: Session,
    ) -> tuple[int, float]:
        """Compute total tokens used and usage percentage of context limit.

        Returns (tokens_used, usage_percent).
        """
        tokens_used = (
            session.total_input_tokens
            + session.total_output_tokens
        )
        if self._model_context_limit <= 0:
            return tokens_used, 0.0
        usage_percent = tokens_used / self._model_context_limit
        return tokens_used, usage_percent

    async def _check_agent_context(
        self,
        session: Session,
        session_id: str,
        task_id: UUID,
    ) -> None:
        """Check agent session context usage and emit warnings or trigger handoff.

        Emits ContextWarning at 80% usage. Triggers handoff at 90%.
        """
        tokens_used, usage_percent = self._compute_context_usage(session)

        if usage_percent >= 0.9:
            _logger.warning(
                "Agent session %s at %.0f%% context"
                " (%d/%d tokens), triggering handoff",
                session_id,
                usage_percent * 100,
                tokens_used,
                self._model_context_limit,
            )
            await self._trace_writer.write_event(
                run_id=self._run_id,
                event_type="context_warning",
                data={
                    "session_id": session_id,
                    "task_id": str(task_id),
                    "usage_percent": round(usage_percent * 100, 1),
                    "tokens_used": tokens_used,
                    "context_limit": self._model_context_limit,
                    "action": "handoff",
                },
                task_id=task_id,
            )
            await self._agent_handoff(session, task_id)
        elif usage_percent >= 0.8:
            _logger.info(
                "Agent session %s at %.0f%% context"
                " (%d/%d tokens)",
                session_id,
                usage_percent * 100,
                tokens_used,
                self._model_context_limit,
            )
            await self._trace_writer.write_event(
                run_id=self._run_id,
                event_type="context_warning",
                data={
                    "session_id": session_id,
                    "task_id": str(task_id),
                    "usage_percent": round(usage_percent * 100, 1),
                    "tokens_used": tokens_used,
                    "context_limit": self._model_context_limit,
                    "action": "warning",
                },
                task_id=task_id,
            )

    async def _agent_handoff(
        self,
        session: Session,
        task_id: UUID,
    ) -> None:
        """Perform agent context handoff: summarize conversation, create new session.

        Asks the current session to summarize, then creates a fresh
        session with the summary as initial context. Replaces the
        session in _task_sessions.
        """
        # Ask the agent to summarize
        from orxtra.scheduler._prompt_templates import (  # noqa: PLC0415
            load_template,
        )

        summary_parts: list[str] = []
        async for event in session.send(
            load_template("handoff_request"),
        ):
            if isinstance(event, Result):
                summary_parts.append(event.text)

        summary = "".join(summary_parts)
        if not summary:
            summary = "Previous conversation context was lost."

        # Create a new session with the same parameters
        task_spec = self._task_specs.get(task_id)
        if task_spec is None:
            return

        new_session, new_session_id = await self._create_agent_session(
            task_spec, task_id,
            1,  # Reset attempt counter for the new session
        )

        # Inject summary as initial context
        summary_prompt = render_template(
            "handoff_resume",
            {"summary": summary},
        )
        # Send the summary to prime the new session
        async for event in new_session.send(summary_prompt):
            if isinstance(event, Result):
                pass  # Consume the response

        # Replace the old session with the new one
        self._task_sessions[task_id] = new_session
        _logger.info(
            "Agent handoff complete for task %s:"
            " old session -> %s",
            task_id,
            new_session_id,
        )

    async def _create_agent_session(  # noqa: C901, PLR0915
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

        # Resolve provider + model: explicit on agent takes
        # precedence, then task-level category override, then
        # agent-level category.
        if (
            agent_def.provider is not None
            and agent_def.model is not None
        ):
            provider_name = agent_def.provider
            model = agent_def.model
        else:
            category_str = task.category or agent_def.category
            if category_str is None:
                msg = (
                    f"Agent '{task.agent}' has no category,"
                    " provider, or model"
                )
                raise ValueError(msg)
            resolved = self._categories.get(category_str)
            if resolved is None:
                msg = (
                    f"Category '{category_str}' not found"
                )
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

        raw_tools = self._build_agent_tools(
            agent_def, task_id, session_id,
            task.name, task.agent,
        )

        async def _trace_callback(
            tool_name: str,
            args: dict[str, Any],
            result: str,
            duration_ms: int,
        ) -> None:
            await self._trace_writer.write_event(
                run_id=self._run_id,
                event_type="tool_call",
                data={
                    "session_id": session_id,
                    "task_id": str(task_id),
                    "tool_name": tool_name,
                    "args": args,
                    "result": result,
                    "duration_ms": duration_ms,
                },
                task_id=task_id,
            )

        # Build result-appendix callback for advisory
        # tool-graph suggestions, deduped per session.
        _suggested: set[str] = set()
        registry = self._tool_registry

        def _result_appendix(
            tool_name: str,
        ) -> str | None:
            edges = registry.edges_from(tool_name)
            if not edges:
                return None
            # Collect neighbor names not yet suggested.
            neighbors = []
            for edge in edges:
                target = edge.target_tool
                if target not in _suggested:
                    neighbors.append(target)
            if not neighbors:
                return None
            # Mark as suggested (dedupe).
            _suggested.update(neighbors)
            names_str = ", ".join(sorted(neighbors))
            return render_template(
                "tool_suggestion",
                {"tool_names": names_str},
            )

        # If the agent declares deferred tools, auto-grant
        # load_tools with factory-based lazy building.
        deferred_names = frozenset(agent_def.deferred)
        if deferred_names:
            # Resolve the full allow list so load_tools
            # enforces it.
            metadata = dict(
                self._tool_registry.get_metadata(),
            )
            metadata.update(SYNTHETIC_ENTRIES)
            full_resolved = resolve_allow_list(
                agent_def.allow, metadata,
            )

            # Mutable cell for the session reference.
            # Assigned after session creation below.
            session_cell: list[Session | None] = [None]

            def _build_and_wrap(name: str) -> Tool:
                """Build a tool from the registry and wrap
                it through the pipeline."""
                entry = self._tool_registry.get_entry(name)
                if entry is None:
                    from orxtra.protocols import ToolError  # noqa: PLC0415
                    msg = f"Unknown tool: {name}"
                    raise ToolError(msg)
                # Build deps matching what was used for
                # the initial tool set.
                _deps = ToolDeps(
                    read_root=self._read_root,
                    write_scope=None,
                    write_queue=self._write_queue,
                    stale_tracker=self._stale_tracker,
                    session_id=session_id,
                    trace_writer=self._trace_writer,
                    run_id=self._run_id,
                    task_id=task_id,
                    task_name=task.name,
                    task_agent=task.agent or "",
                    scheduler_ref=self,
                    transport_registry=(
                        self._transport_registry
                    ),
                    categories=self._categories,
                    agents=self._agents,
                    preview_threshold=10000,
                    preview_lines=50,
                )
                raw = entry.factory(_deps)
                return wrap_tool_with_pipeline(
                    tool=raw,
                    scheduler_check=(
                        self.check_active_task
                    ),
                    secret_registry=self._secret_registry,
                    trace_callback=_trace_callback,
                    session_id=session_id,
                    is_start_task=False,
                    is_file_mutation=(
                        "mutation" in raw.tags
                    ),
                    mutation_tracker=(
                        self._session_mutations
                    ),
                    result_appendix=_result_appendix,
                )

            def _get_tools() -> list[Tool]:
                s = session_cell[0]
                if s is None:
                    return []
                return s.tools

            def _set_tools(tools: list[Tool]) -> None:
                s = session_cell[0]
                if s is not None:
                    s.update_tools(tools)

            lt = make_load_tools_tool(
                allowed_names=frozenset(full_resolved),
                build_tool=_build_and_wrap,
                get_session_tools=_get_tools,
                set_session_tools=_set_tools,
            )
            raw_tools.append(lt)

        tools = wrap_tools_for_session(
            tools=raw_tools,
            scheduler_check=self.check_active_task,
            secret_registry=self._secret_registry,
            trace_callback=_trace_callback,
            session_id=session_id,
            mutation_tracker=self._session_mutations,
            result_appendix=_result_appendix,
        )

        previous_session_id: str | None = None
        if (
            attempt > 1
            and task.retry_resume
            and task_id in self._task_sessions
        ):
            prev = self._task_sessions[task_id]
            previous_session_id = prev.session_id

        session = await create_session(
            transport=transport,
            model=model,
            system_prompt=agent_def.prompt,
            tools=tools,
            trace_writer=self._trace_writer,
            run_id=self._run_id,
            session_id=previous_session_id,
            pool=self._pool,
            backend=self._backend,
        )

        # Wire the session reference into load_tools'
        # closures so it can read/update the tool set.
        if deferred_names:
            session_cell[0] = session

        return session, session_id

    def _build_agent_tools(  # noqa: C901
        self,
        agent_def: Agent,
        task_id: UUID,
        session_id: str,
        task_name: str,
        task_agent: str,
    ) -> list[Tool]:
        """Build raw tools based on agent's allow list.

        Uses the ToolRegistry for data-driven construction of
        standard tools, with special handling for git (subcommand
        resolution), consult (needs already-built tools), inline
        tool definitions (per-agent [[tools.define]]),
        deferred tools (stubs + load_tools auto-grant), and
        lifecycle tools (always present).
        """
        preview_threshold = 10000
        preview_lines = 50

        # Build metadata including special tools for
        # wildcard/tag resolution.
        metadata = dict(self._tool_registry.get_metadata())
        metadata.update(SYNTHETIC_ENTRIES)

        # Resolve allow list to concrete tool names.
        resolved = resolve_allow_list(
            agent_def.allow, metadata,
        )

        # Determine which tools are deferred for this agent.
        deferred_names = frozenset(agent_def.deferred)

        # Build standard tools from registry, EXCLUDING
        # deferred tools (those get stubs instead).
        non_deferred_resolved = resolved - deferred_names

        deps = ToolDeps(
            read_root=self._read_root,
            write_scope=None,
            write_queue=self._write_queue,
            stale_tracker=self._stale_tracker,
            session_id=session_id,
            trace_writer=self._trace_writer,
            run_id=self._run_id,
            task_id=task_id,
            task_name=task_name,
            task_agent=task_agent,
            scheduler_ref=self,
            transport_registry=self._transport_registry,
            categories=self._categories,
            agents=self._agents,
            preview_threshold=preview_threshold,
            preview_lines=preview_lines,
        )
        raw_tools = self._tool_registry.build_tools(
            non_deferred_resolved, deps,
        )

        # Add deferred tool stubs: lightweight Tool objects
        # with deferred=True. The LLM sees them in the tool
        # list but with minimal schema. The real tool is
        # built on demand via load_tools.
        for d_name in sorted(deferred_names):
            entry = self._tool_registry.get_entry(d_name)
            if entry is None:
                continue
            raw_tools.append(Tool(
                name=d_name,
                description=entry.description or (
                    f"Deferred tool: {d_name}"
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                },
                execute=_deferred_stub_execute,
                namespace=entry.namespace,
                tags=entry.tags,
                deferred=True,
            ))

        # Git tool (subcommands depend on write access).
        if "git" in resolved:
            has_write = bool(resolved & WRITE_TOOL_NAMES)
            subcommands = list(
                ["status", "diff", "log", "show",
                 "blame", "branches",
                 "changed_files"]
                + (["commit"] if has_write else []),
            )
            raw_tools.append(make_git_tool(
                self._read_root, subcommands,
                run_context={
                    "run_id": str(self._run_id),
                    "task_id": str(task_id),
                },
            ))

        # Consult (needs already-built tools).
        if "consult" in resolved:
            consult_registry: dict[str, Tool] = {
                t.name: t for t in raw_tools
            }
            raw_tools.append(make_consult_tool(
                tool_registry=consult_registry,
                transport_registry=(
                    self._transport_registry
                ),
                trace_writer=self._trace_writer,
                run_id=self._run_id,
                read_root=self._read_root,
                categories=self._categories,
                agents=self._agents,
            ))

        # Inline tool definitions (per-agent [[tools.define]]).
        if agent_def.inline_tools:
            from orxtra.tool import DataToolDefinition  # noqa: PLC0415

            for itd in agent_def.inline_tools:
                # Build a DataToolDefinition from the inline
                # raw dict to get proper validation.
                defn_dict = {
                    "name": itd.name,
                    "description": itd.description,
                    "namespace": itd.namespace,
                    "deferred": itd.deferred,
                    "tags": itd.tags,
                    "params": itd.params or {},
                    "execution": itd.execution,
                    "output": itd.output,
                }
                defn = DataToolDefinition(**defn_dict)

                # Only build if the tool name resolves
                # through the allow list (explicit name or
                # namespace wildcard or tag filter).
                if itd.name not in resolved:
                    # Check namespace wildcard match.
                    ns_match = any(
                        entry.endswith(".*")
                        and itd.namespace.startswith(
                            entry[:-2],
                        )
                        for entry in agent_def.allow
                    )
                    if not ns_match and "*" not in agent_def.allow:
                        continue

                from orxtra.tool import (  # noqa: PLC0415
                    CommandExecution,
                    HttpExecution,
                    MontyExecution,
                    build_command_tool,
                    build_http_tool,
                    build_monty_tool,
                )

                if isinstance(defn.execution, CommandExecution):
                    raw_tools.append(
                        build_command_tool(defn, deps),
                    )
                elif isinstance(defn.execution, MontyExecution):
                    raw_tools.append(
                        build_monty_tool(defn, deps),
                    )
                elif isinstance(defn.execution, HttpExecution):
                    raw_tools.append(
                        build_http_tool(defn, deps),
                    )

        # Always add lifecycle tools.
        raw_tools.extend([
            make_start_task_tool(self, session_id),
            make_end_task_tool(self, session_id),
            make_create_task_tool(self, session_id),
            make_create_workflow_tool(
                self, session_id,
            ),
            make_create_wait_for_tool(
                self, session_id,
            ),
            make_await_task_tool(self, session_id),
        ])

        return raw_tools

    async def _assemble_agent_prompt(  # noqa: PLR0913
        self,
        task: TaskSpec,
        task_id: UUID,
        variables: dict[str, Any] | None,
        attempt: int,
        attempt_id: UUID,
        prior_attempts: list[dict[str, Any]],
    ) -> str:
        """Assemble full prompt with runtime context layers.

        Uses the compose engine with fragment providers for each layer.
        Variable substitution in the task prompt uses strict resolution:
        unresolved placeholders raise ValueError. Unused variables are
        filtered (workflow executor accumulates dependency outputs).
        """
        assert task.task_prompt is not None  # noqa: S101
        resolved_prompt = _resolve_task_variables(
            task.task_prompt, variables,
        )

        context: dict[str, Any] = {
            "task_id": str(task_id),
            "task_prompt": resolved_prompt,
            "constraints": list(self._active_constraints),
            "notepad_entries": list(self._notepad_entries),
            "lessons": list(self._lessons),
            "attempt": attempt,
            "retry_inject_failure": bool(
                task.retry_inject_failure,
            ),
            "prior_attempts": list(prior_attempts),
        }

        engine = CompositionEngine(
            providers=[
                TaskPreambleProvider(),
                TaskPromptProvider(),
                ConstraintsProvider(),
                NotepadProvider(),
                LessonsProvider(),
                FailureContextProvider(),
            ],
            separator="\n\n",
        )

        prompt = engine.compose(context)

        # Layer 3: Overseer context refinement
        if (
            task.context_refinement
            and self._overseer_interface is not None
            and hasattr(
                self._overseer_interface,
                "refine_context",
            )
        ):
            pre_refinement = prompt
            refined = await (
                self._overseer_interface
                .refine_context(
                    task.name, prompt,
                )
            )
            if refined != prompt:
                import difflib  # noqa: PLC0415

                diff = "\n".join(
                    difflib.unified_diff(
                        prompt.splitlines(),
                        refined.splitlines(),
                        fromfile="pre-refinement",
                        tofile="post-refinement",
                        lineterm="",
                    )
                )
                await (
                    self._trace_writer
                    .write_context_diff(
                        attempt_id,
                        pre_refinement,
                        diff,
                    )
                )
                prompt = refined

        return prompt

    async def _refresh_injection_data(self) -> None:
        """Refresh constraints, lessons, and notepad from trace.

        Called at the start of each task attempt, before prompt
        assembly. Each callback replaces the scheduler's in-memory
        list with fresh data from the storage backend.

        When no callbacks are registered (default), the lists
        remain as initialized (empty or manually populated).
        """
        if self._refresh_constraints is not None:
            self._active_constraints = (
                await self._refresh_constraints(
                    self._run_id,
                )
            )
        if self._refresh_lessons is not None:
            self._lessons = (
                await self._refresh_lessons(self._run_id)
            )
        if self._refresh_notepad is not None:
            self._notepad_entries = (
                await self._refresh_notepad(self._run_id)
            )

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

    async def _auto_commit(
        self,
        session_id: str,
        message: str,
    ) -> None:
        tracked_files = self._session_mutations.get(
            session_id, set(),
        )
        mutations_detected = bool(tracked_files)
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
                has_generic = "__generic__" in tracked_files
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
                        if has_generic or fname in tracked_files:
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
