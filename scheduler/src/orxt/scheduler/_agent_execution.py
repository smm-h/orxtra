from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxt.notepad import format_notepad
from orxt.protocols._execution import CheckResult
from orxt.protocols._task import (
    EscalationPayload,
    TaskResult,
    TaskSpec,
    TaskState,
)
from orxt.session import Session, create_session
from orxt.tool._consult_tool import make_consult_tool
from orxt.tool._exec_tool import make_exec_tool
from orxt.tool._git_tool import make_git_tool
from orxt.tool._http_tool import make_http_tool
from orxt.tool._shell_tool import make_shell_tool
from orxt.tool._notepad_tool import make_notepad_tool
from orxt.tool._pipeline import wrap_tools_for_session
from orxt.tool._read_tools import (
    make_diff_tool,
    make_glob_tool,
    make_grep_tool,
    make_list_dir_tool,
    make_read_tool,
    make_stat_tool,
)
from orxt.tool._task_tools import (
    make_await_task_tool,
    make_create_task_tool,
    make_create_wait_for_tool,
    make_create_workflow_tool,
    make_end_task_tool,
    make_start_task_tool,
)
from orxt.tool._write_tools import (
    make_copy_tool,
    make_delete_tool,
    make_edit_tool,
    make_mkdir_tool,
    make_move_tool,
    make_set_executable_tool,
    make_write_tool,
)
from orxt.transport import Result, Usage

if TYPE_CHECKING:
    from orxt.agent import Agent
    from orxt.protocols._tool import Tool

from orxt.scheduler._base import SchedulerBase

_logger = logging.getLogger("orxt.scheduler")


class AgentExecutionMixin(SchedulerBase):
    """Mixin for agent and orchestrator task execution."""

    async def _execute_orchestrator_task(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        """Execute an orchestrator task with multi-turn suspension support."""
        from orxt.transport import SessionSuspended  # noqa: PLC0415

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

        prompt = self._resolve_prompt(
            task.task_prompt or "",
            variables or {},
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

            resume_msg = (
                f"Child task {child_task_id_str} completed."
                f" Result: {child_result.output or 'no output'}"
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

                from orxt.protocols._events import (  # noqa: PLC0415
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

            try:
                if task.timeout is not None:
                    await asyncio.wait_for(
                        self._run_session(
                            session,
                            prompt,
                            session_id,
                            task_id,
                            stream_deltas=bool(
                                task.stream_deltas,
                            ),
                        ),
                        timeout=float(task.timeout),
                    )
                else:
                    await self._run_session(
                        session,
                        prompt,
                        session_id,
                        task_id,
                        stream_deltas=bool(
                            task.stream_deltas,
                        ),
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
                from orxt.scheduler._executor import classify_error  # noqa: PLC0415
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
        parent_session = (
            self._task_sessions.get(parent_task_id)
            if parent_task_id is not None
            else None
        )
        if (
            parent_session is not None
            and self._task_states.get(parent_task_id) == TaskState.ACTIVE
        ):
            escalation_msg = (
                f"[ESCALATION] Task '{task.name}' exhausted"
                f" {max_attempts} attempt(s). "
                "Failed checks:"
                f" {[cr.message for cr in check_results if not cr.passed]}. "
                f"Agent summary: Retries exhausted."
            )
            async for _ in parent_session.send(escalation_msg):
                pass
        else:
            from orxt.protocols._events import (  # noqa: PLC0415
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
        session_id: str,  # noqa: ARG002
        task_id: UUID,  # noqa: ARG002
        *,
        stream_deltas: bool = False,
    ) -> str:
        result_text = ""
        async for event in session.send(prompt, stream_deltas=stream_deltas):
            if isinstance(event, Result):
                result_text = event.text
        return result_text

    async def _create_agent_session(
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

        tools = wrap_tools_for_session(
            tools=raw_tools,
            scheduler_check=self.check_active_task,
            secret_registry=self._secret_registry,
            trace_callback=_trace_callback,
            session_id=session_id,
            mutation_tracker=self._session_mutations,
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
        )
        return session, session_id

    def _build_agent_tools(
        self,
        agent_def: Agent,
        task_id: UUID,
        session_id: str,
        task_name: str,
        task_agent: str,
    ) -> list[Tool]:
        """Build raw tools based on agent's allow list."""
        agent_allow = set(agent_def.allow)
        raw_tools: list[Tool] = []

        # Preview configuration
        preview_threshold = 10000
        preview_lines = 50

        # Read tools
        if "read" in agent_allow:
            raw_tools.append(make_read_tool(
                self._read_root, preview_threshold,
                preview_lines,
                session_id=session_id,
            ))
        if "list_dir" in agent_allow:
            raw_tools.append(
                make_list_dir_tool(self._read_root),
            )
        if "glob" in agent_allow:
            raw_tools.append(
                make_glob_tool(self._read_root),
            )
        if "grep" in agent_allow:
            raw_tools.append(make_grep_tool(
                self._read_root, preview_threshold,
                preview_lines,
            ))
        if "stat" in agent_allow:
            raw_tools.append(
                make_stat_tool(self._read_root),
            )
        if "diff" in agent_allow:
            raw_tools.append(
                make_diff_tool(self._read_root),
            )

        # Write tools (with write-safety)
        if "write" in agent_allow:
            raw_tools.append(make_write_tool(
                self._read_root, None,
                self._write_queue,
                self._stale_tracker, session_id,
            ))
        if "edit" in agent_allow:
            raw_tools.append(make_edit_tool(
                self._read_root, None,
                self._write_queue,
                self._stale_tracker, session_id,
            ))
        if "mkdir" in agent_allow:
            raw_tools.append(
                make_mkdir_tool(self._read_root, None),
            )
        if "move" in agent_allow:
            raw_tools.append(make_move_tool(
                self._read_root, None,
                self._write_queue,
                self._stale_tracker, session_id,
            ))
        if "copy" in agent_allow:
            raw_tools.append(make_copy_tool(
                self._read_root, None,
                self._write_queue,
                self._stale_tracker, session_id,
            ))
        if "delete" in agent_allow:
            raw_tools.append(
                make_delete_tool(self._read_root, None),
            )
        if "set_executable" in agent_allow:
            raw_tools.append(
                make_set_executable_tool(
                    self._read_root, None,
                ),
            )

        # Git tool
        if "git" in agent_allow:
            write_tools = {
                "write", "edit", "delete", "move",
                "copy", "mkdir", "set_executable",
            }
            has_write = bool(agent_allow & write_tools)
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

        # Notepad
        if "notepad" in agent_allow:
            raw_tools.append(make_notepad_tool(
                self._trace_writer,
                str(self._run_id),
                task_name,
                task_agent,
            ))

        # HTTP
        if "http" in agent_allow:
            raw_tools.append(make_http_tool(
                allowed_hosts="allow_all",
            ))

        # Consult
        if "consult" in agent_allow:
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

        # Exec tools (per-agent configured executables)
        if "exec" in agent_allow and agent_def.exec_tools:
            for exec_config in agent_def.exec_tools:
                raw_tools.append(make_exec_tool(
                    executable=exec_config.executable,
                    description=exec_config.description,
                    arg_schema={},
                    read_root=self._read_root,
                    timeout_ceiling=(
                        exec_config.timeout_ceiling
                    ),
                    preview_threshold=preview_threshold,
                    preview_lines=preview_lines,
                ))

        # Shell tool (per-agent configured shell access)
        if (
            "shell" in agent_allow
            and agent_def.shell_config
        ):
            raw_tools.append(make_shell_tool(
                allowed_binaries=(
                    agent_def.shell_config
                    .allowed_binaries
                ),
                description=(
                    agent_def.shell_config.description
                ),
                read_root=self._read_root,
                timeout_ceiling=(
                    agent_def.shell_config
                    .timeout_ceiling
                ),
                preview_threshold=preview_threshold,
                preview_lines=preview_lines,
            ))

        # Always add lifecycle tools
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

    async def _assemble_agent_prompt(
        self,
        task: TaskSpec,
        task_id: UUID,
        variables: dict[str, Any] | None,
        attempt: int,
        attempt_id: UUID,
        prior_attempts: list[dict[str, Any]],
    ) -> str:
        """Assemble full prompt with runtime context layers."""
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

        # Lessons from previous runs
        if self._lessons:
            fresh = [
                lesson for lesson in self._lessons
                if not lesson.get("stale", False)
            ]
            stale = [
                lesson for lesson in self._lessons
                if lesson.get("stale", False)
            ]
            if fresh:
                prompt += (
                    "\n\n## Lessons (verified)"
                )
                for lesson in fresh:
                    prompt += (
                        f"\n- {lesson['text']}"
                    )
            if stale:
                prompt += (
                    "\n\n## Lessons (may be stale)"
                )
                for lesson in stale:
                    prompt += (
                        f"\n- {lesson['text']}"
                        f" [stale: source modified"
                        f" after lesson was created]"
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
