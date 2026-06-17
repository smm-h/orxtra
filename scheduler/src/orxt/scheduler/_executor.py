from __future__ import annotations

import asyncio
import contextlib
import errno
import importlib
import json
import logging
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from orxt.notepad import NotepadEntry, format_notepad
from orxt.protocols._errors import ErrorCategory
from orxt.protocols._events import StructuralAdvisory
from orxt.protocols._execution import CheckResult
from orxt.protocols._task import (
    AttemptSummary,
    BudgetExhaustionPolicy,
    TaskContext,
    TaskResult,
    TaskSpec,
    TaskState,
)
from orxt.scheduler._agent_execution import AgentExecutionMixin
from orxt.scheduler._enforcement import EnforcementMixin
from orxt.scheduler._events import EventRegistry
from orxt.scheduler._graph import (
    build_graph,
    find_parallel_groups,
    topological_sort,
)
from orxt.scheduler._lifecycle_handlers import LifecycleHandlersMixin
from orxt.scheduler._locks import FileLockRegistry
from orxt.scheduler._services import (
    ServiceInstance,
    check_health,
    start_service,
    stop_service,
)
from orxt.scheduler._task_dispatch import TaskDispatchMixin
from orxt.scheduler._validator import validate_task_tree
from orxt.transport import Result
from orxt.write_safety import StaleWriteTracker, WriteQueue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path
    from uuid import UUID

    import asyncpg
    from orxt.agent import Agent
    from orxt.protocols._task import Execution
    from orxt.scheduler._overseer import (
        OverseerEvent,
        OverseerInterface,
    )
    from orxt.scheduler._types import WorkflowConfig
    from orxt.secrets._registry import SecretRegistry
    from orxt.session import Session
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


class Scheduler(
    AgentExecutionMixin,
    TaskDispatchMixin,
    LifecycleHandlersMixin,
    EnforcementMixin,
):
    def __init__(  # noqa: PLR0913
        self,
        trace_writer: TraceWriter,
        transport_registry: dict[str, Transport],
        agents: dict[str, Agent],
        categories: dict[str, str],
        run_id: UUID,
        read_root: Path,
        *,
        pool: asyncpg.Pool | None = None,
        overseer_interface: OverseerInterface | None = None,
        knowledge_dir: Path | None = None,
        model_context_limit: int = 200_000,
        knowledge_loader: Callable[[Path, Any, UUID], Awaitable[None]] | None = None,
        handoff_checker: Callable[[Any, int], Awaitable[bool]] | None = None,
        handoff_performer: Callable[[Any, Any, UUID], Awaitable[Any]] | None = None,
        budget_exhaustion_policy: BudgetExhaustionPolicy = (
            BudgetExhaustionPolicy.UNLIMITED
        ),
        budget_limit: Decimal | None = None,
        autonomy_level: str = "max",
        secret_registry: SecretRegistry | None = None,
    ) -> None:
        self._trace_writer = trace_writer
        self._pool = pool
        self._transport_registry = transport_registry
        self._agents = agents
        self._categories = categories
        self._run_id = run_id
        self._read_root = read_root
        self._overseer_interface = overseer_interface
        self._knowledge_dir = knowledge_dir
        self._model_context_limit = model_context_limit
        self._knowledge_loader = knowledge_loader
        self._handoff_checker = handoff_checker
        self._handoff_performer = handoff_performer
        self._budget_exhaustion_policy = budget_exhaustion_policy
        self._budget_limit = budget_limit
        self._autonomy_level = autonomy_level
        self._secret_registry = secret_registry
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
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._event_registry = EventRegistry()
        self._session_mutations: dict[str, set[str]] = {}
        self._write_queue = WriteQueue()
        self._stale_tracker = StaleWriteTracker()
        self._notepad_entries: list[NotepadEntry] = []
        self._lessons: list[dict[str, Any]] = []
        self._active_constraints: list[tuple[str, str]] = []
        self._mechanical_constraints: list[tuple[str, str]] = []
        self._pending_end_task_message: dict[UUID, str] = {}
        self._file_lock_registry = FileLockRegistry()
        self._service_instances: list[ServiceInstance] = []
        self._pending_advisories: list[
            dict[str, Any]
        ] = []
        self._pending_await: dict[str, str] = {}
        self._paused = asyncio.Event()
        self._paused.set()  # Not paused initially
        self._budget_threshold_events: list[
            tuple[UUID, str, Decimal, Decimal]
        ] = []
        self._budget_exhausted_events: list[
            tuple[UUID, str, Decimal, Decimal]
        ] = []

    async def run_consult(
        self,
        agent: str,
        question: str,
        variable_values: dict[str, str] | None = None,
    ) -> str:
        agent_def = self._agents.get(agent)
        if agent_def is None:
            msg = f"Agent '{agent}' not found"
            raise ValueError(msg)

        # Resolve model from agent category
        category_str = agent_def.category
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

        # Substitute variables into question
        resolved_question = question
        if variable_values:
            for k, v in variable_values.items():
                resolved_question = (
                    resolved_question.replace(
                        f"{{{k}}}", v,
                    )
                )

        # Call transport directly (no session needed
        # for read-only consult)
        result_text = ""
        async for event in transport.send(
            resolved_question,
            model=model,
            system_prompt=agent_def.prompt,
            tools=[],
        ):
            if isinstance(event, Result):
                result_text = event.text
        return result_text

    async def run_workflow_check(
        self,
        execution: Execution,
    ) -> CheckResult:
        from orxt.protocols._task import (  # noqa: PLC0415
            WorkflowExecution,
        )

        if not isinstance(execution, WorkflowExecution):
            return CheckResult(
                passed=False,
                message=(
                    "Expected WorkflowExecution, got"
                    f" {type(execution).__name__}"
                ),
            )

        if not execution.tasks:
            return CheckResult(
                passed=True,
                message="Workflow check passed (no tasks)",
            )

        # Build a composite task spec from the workflow
        composite = TaskSpec(
            name=f"check_{execution.name}",
            subtasks=list(execution.tasks),
            postchecks=list(execution.postchecks),
        )

        result = await self._execute_composite_task(
            composite,
            await self._trace_writer.create_task(
                run_id=self._run_id,
                parent_task_id=None,
                name=composite.name,
                task_type="workflow",
            ),
        )

        all_passed = all(
            cr.passed for cr in result.check_results
        )
        if all_passed:
            return CheckResult(
                passed=True,
                message=(
                    "Workflow check"
                    f" '{execution.name}' passed"
                ),
            )
        failed = [
            cr.message
            for cr in result.check_results
            if not cr.passed
        ]
        return CheckResult(
            passed=False,
            message=(
                "Workflow check"
                f" '{execution.name}' failed:"
                f" {'; '.join(failed)}"
            ),
        )

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
        await self._crash_recovery()

        # Subscribe to run control signals (pause/abort)
        await self._trace_writer.subscribe_run_control(
            self._run_id, self._handle_control_signal,
        )
        # Bridge trace events to EventRegistry
        await self._bridge_trace_to_events()

        pg_listener_task, pg_listener_conn, on_notification = (
            await self._setup_pg_listener()
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

        # Start services
        for svc_config in config.services:
            instance = await start_service(
                svc_config, self._read_root,
            )
            self._service_instances.append(instance)

        try:
            task_id_map = await self._register_workflow_tasks(
                config,
            )
            await self._execute_task_groups(
                config, task_id_map,
            )

            # Coherence summary at run end
            await self._write_coherence_summary()
        finally:
            await self._stop_services()

        # Clean up PG listener
        await self._cleanup_pg_listener(
            pg_listener_task, pg_listener_conn,
            on_notification,
        )

        await self._trace_writer.unsubscribe_run_control(
            self._run_id,
        )

    async def _crash_recovery(self) -> None:
        """Three-pass idempotent crash recovery startup."""
        if self._pool is None:
            return
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

    async def _setup_pg_listener(
        self,
    ) -> tuple[asyncio.Task[None] | None, asyncpg.Connection | None, object]:
        """Set up cross-process signal delivery via PG LISTEN.

        Returns (listener_task, listener_conn,
        notification_callback).
        """
        if self._pool is None:
            return None, None, None

        pg_listener_conn = await self._pool.acquire()

        def _on_notification(
            conn: object,
            pid: int,
            channel: str,
            payload: str,
        ) -> None:
            _ = conn, pid, channel
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                _logger.warning(
                    "Invalid PG notification payload:"
                    " %s",
                    payload,
                )
                return
            event_type = msg.get("event_type", "")
            run_id_str = msg.get("run_id", "")
            if (
                event_type == "run_state"
                and run_id_str == str(self._run_id)
            ):
                task = asyncio.create_task(
                    self._handle_control_signal(
                        self._run_id,
                        msg.get("new_status", ""),
                    ),
                )
                self._background_tasks.add(task)
                task.add_done_callback(
                    self._background_tasks.discard,
                )
            else:
                task = asyncio.create_task(
                    self._event_registry.fire(
                        event_type,
                        msg.get("data"),
                    ),
                )
                self._background_tasks.add(task)
                task.add_done_callback(
                    self._background_tasks.discard,
                )

        await pg_listener_conn.add_listener(
            "orxt_events", _on_notification,
        )

        async def _listen_forever() -> None:
            stop = asyncio.Event()
            await stop.wait()

        pg_listener_task = asyncio.create_task(
            _listen_forever(),
        )
        return pg_listener_task, pg_listener_conn, _on_notification

    async def _cleanup_pg_listener(
        self,
        pg_listener_task: asyncio.Task[None] | None,
        pg_listener_conn: asyncpg.Connection | None,
        on_notification: object,
    ) -> None:
        """Clean up PG listener resources."""
        if pg_listener_task is not None:
            pg_listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pg_listener_task
        if pg_listener_conn is not None:
            await pg_listener_conn.remove_listener(
                "orxt_events", on_notification,
            )
            await self._pool.release(pg_listener_conn)

    async def _register_workflow_tasks(
        self,
        config: WorkflowConfig,
    ) -> dict[str, UUID]:
        """Validate config and register all workflow tasks."""
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
            self._init_task_state(
                task_id, task, parent=None,
            )
        return task_id_map

    async def _execute_task_groups(
        self,
        config: WorkflowConfig,
        task_id_map: dict[str, UUID],
    ) -> None:
        """Execute task groups in topological order."""
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
                t.add_done_callback(
                    self._running_tasks.discard,
                )
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
                    cr.passed
                    for cr in result.check_results
                )
                tid = task_id_map[name]
                start = self._task_start_times.get(tid)
                duration = (
                    time.monotonic() - start
                    if start is not None
                    else 0.0
                )
                meta = self._get_scoped_results_meta(
                    None,
                ).get(name, {})
                retries = meta.get("retries_used", 0)
                variables[f"{name}_result"] = {
                    "passed": all_passed,
                    "duration_seconds": duration,
                    "retries_used": retries,
                }

            # Check for escalated tasks in this group
            escalated_in_group = [
                name for name in group
                if self._task_states.get(task_id_map[name]) == TaskState.ESCALATED
            ]
            if escalated_in_group:
                from orxt.scheduler._types import EscalationPolicy  # noqa: PLC0415

                policy = config.escalation_policy
                if policy == EscalationPolicy.HALT:
                    self._paused.clear()
                    break
                if policy == EscalationPolicy.ABORT_ALL:
                    await self.abort()
                    break
                # CONTINUE_INDEPENDENT: do nothing, continue loop

            # Health-check services between groups
            for svc in self._service_instances:
                if not await check_health(svc):
                    _logger.warning(
                        "Service %s unhealthy",
                        svc.config.name,
                    )

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
        return await self._execute_orchestrator_or_agent_task(
            task, task_id, parent_task_id, variables,
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

    async def _stop_services(self) -> None:
        """Stop all running service instances."""
        for instance in self._service_instances:
            await stop_service(instance)
        self._service_instances.clear()

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
        await self._stop_services()

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

    async def _handle_control_signal(
        self, run_id: UUID, new_status: str,  # noqa: ARG002
    ) -> None:
        """Handle a control signal from the trace layer."""
        if new_status == "aborted":
            await self.abort()
        elif new_status == "paused":
            await self.pause()

    async def _bridge_trace_to_events(self) -> None:
        """Bridge trace event callbacks to the EventRegistry.

        When the TraceWriter has an event_callback, this wraps it
        so that events also fire on the in-process EventRegistry.
        This makes wait-for tasks work when fire_event inserts
        an event row via the trace layer.
        """
        original_callback = self._trace_writer._event_callback  # noqa: SLF001

        async def _bridged_callback(
            event_id: UUID,
            run_id: UUID,
            event_type: str,
            data: dict[str, Any],
        ) -> None:
            if original_callback is not None:
                await original_callback(
                    event_id, run_id, event_type, data,
                )
            # Forward to EventRegistry for wait-for tasks
            await self._event_registry.fire(
                event_type, data,
            )

        self._trace_writer._event_callback = _bridged_callback  # noqa: SLF001

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
                FALLBACK_HANDLERS,
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
            handler = FALLBACK_HANDLERS.get(behavior)
            if handler is not None:
                await handler(
                    event,
                    _logger,
                    trace_writer=self._trace_writer,
                    run_id=self._run_id,
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
        import uuid as _uuid  # noqa: PLC0415

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
