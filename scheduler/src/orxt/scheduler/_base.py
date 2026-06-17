"""Abstract base class for Scheduler mixins.

Declares all shared attributes and cross-mixin methods so mypy can resolve
references in any mixin without seeing the concrete ``Scheduler`` class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable
    from decimal import Decimal
    from pathlib import Path
    from uuid import UUID

    import asyncpg
    from orxt.agent import Agent
    from orxt.notepad import NotepadEntry
    from orxt.protocols._execution import CheckResult
    from orxt.protocols._task import (
        BudgetExhaustionPolicy,
        Execution,
        TaskContext,
        TaskResult,
        TaskSpec,
        TaskState,
    )
    from orxt.scheduler._events import EventRegistry
    from orxt.scheduler._locks import FileLockRegistry
    from orxt.scheduler._overseer import OverseerEvent, OverseerInterface
    from orxt.scheduler._services import ServiceInstance
    from orxt.secrets._registry import SecretRegistry
    from orxt.session import Session
    from orxt.trace import TraceWriter
    from orxt.transport import Transport, Usage
    from orxt.write_safety import StaleWriteTracker, WriteQueue


class SchedulerBase(ABC):
    """Abstract base declaring every attribute and cross-mixin method.

    Each mixin inherits from this so mypy can resolve ``self._*`` attributes
    and method calls that cross mixin boundaries.
    """

    # ------------------------------------------------------------------
    # Attributes (type-only declarations, no defaults)
    # ------------------------------------------------------------------
    _trace_writer: TraceWriter
    _pool: asyncpg.Pool | None
    _transport_registry: dict[str, Transport]
    _agents: dict[str, Agent]
    _categories: dict[str, str]
    _run_id: UUID
    _read_root: Path
    _overseer_interface: OverseerInterface | None
    _knowledge_dir: Path | None
    _model_context_limit: int
    _knowledge_loader: Callable[[Path, Any, UUID], Awaitable[None]] | None
    _handoff_checker: Callable[[Any, int], Awaitable[bool]] | None
    _handoff_performer: Callable[[Any, Any, UUID], Awaitable[Any]] | None
    _budget_exhaustion_policy: BudgetExhaustionPolicy
    _budget_limit: Decimal | None
    _autonomy_level: str
    _secret_registry: SecretRegistry | None
    _active_tasks: dict[str, UUID]
    _task_states: dict[UUID, TaskState]
    _task_specs: dict[UUID, TaskSpec]
    _task_parents: dict[UUID, UUID | None]
    _task_children: dict[UUID, list[UUID]]
    _task_outputs: dict[UUID | None, dict[str, str | None]]
    _task_structured_outputs: dict[UUID | None, dict[str, dict[str, Any] | None]]
    _task_results_meta: dict[UUID | None, dict[str, dict[str, Any]]]
    _task_costs: dict[UUID, Decimal]
    _task_start_times: dict[UUID, float]
    _task_sessions: dict[UUID, Session]
    _running_tasks: set[asyncio.Task[Any]]
    _background_tasks: set[asyncio.Task[Any]]
    _event_registry: EventRegistry
    _session_mutations: dict[str, set[str]]
    _write_queue: WriteQueue
    _stale_tracker: StaleWriteTracker
    _notepad_entries: list[NotepadEntry]
    _lessons: list[dict[str, Any]]
    _active_constraints: list[tuple[str, str]]
    _constraint_checkers: dict[str, Callable[..., Awaitable[CheckResult]]]
    _mechanical_constraints: list[tuple[str, str]]
    _pending_end_task_message: dict[UUID, str]
    _file_lock_registry: FileLockRegistry
    _service_instances: list[ServiceInstance]
    _pending_advisories: list[dict[str, Any]]
    _pending_await: dict[str, str]
    _pre_task_snapshots: dict[UUID, dict[str, Any]]
    _paused: asyncio.Event
    _budget_threshold_events: list[tuple[UUID, str, Decimal, Decimal]]
    _budget_exhausted_events: list[tuple[UUID, str, Decimal, Decimal]]
    _budget_blocked: bool

    # ------------------------------------------------------------------
    # Cross-mixin methods (from _executor.py)
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute_task(
        self,
        task: TaskSpec,
        parent_task_id: UUID | None,
        *,
        task_id: UUID | None = None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult: ...

    @abstractmethod
    def _make_task_context(  # noqa: PLR0913
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        attempt: int,
        prior_attempts: list[dict[str, Any]],
        variables: dict[str, Any] | None,
    ) -> TaskContext: ...

    @abstractmethod
    def _complete_task(  # noqa: PLR0913
        self,
        task_id: UUID,
        task_name: str,
        output: str | None,
        *,
        structured: dict[str, Any] | None = None,
        duration: float = 0.0,
        retries: int = 0,
    ) -> None: ...

    @abstractmethod
    async def _send_overseer_event(
        self, event: OverseerEvent,
    ) -> None: ...

    @abstractmethod
    async def abort(self) -> None: ...

    @abstractmethod
    def _get_scoped_outputs(
        self, parent_id: UUID | None,
    ) -> dict[str, str | None]: ...

    # ------------------------------------------------------------------
    # Cross-mixin methods (from _agent_execution.py)
    # ------------------------------------------------------------------

    @abstractmethod
    async def _execute_orchestrator_or_agent_task(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None,
    ) -> TaskResult: ...

    @abstractmethod
    async def _auto_commit(
        self,
        session_id: str,
        message: str,
    ) -> None: ...

    # ------------------------------------------------------------------
    # Cross-mixin methods (from _task_dispatch.py)
    # ------------------------------------------------------------------

    @abstractmethod
    async def _execute_composite_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult: ...

    @abstractmethod
    async def _execute_for_each(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult: ...

    @abstractmethod
    async def _execute_function_task(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult: ...

    @abstractmethod
    async def _execute_wait_for_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult: ...

    @abstractmethod
    async def _execute_decision_point_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult: ...

    # ------------------------------------------------------------------
    # Cross-mixin methods (from _lifecycle_handlers.py)
    # ------------------------------------------------------------------

    @abstractmethod
    def check_active_task(self, session_id: str) -> UUID: ...

    # ------------------------------------------------------------------
    # Cross-mixin methods (from _enforcement.py)
    # ------------------------------------------------------------------

    @abstractmethod
    async def _run_postchecks(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> list[CheckResult]: ...

    @abstractmethod
    async def _run_prechecks(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> list[CheckResult]: ...

    @abstractmethod
    async def _run_mechanical_constraints(
        self,
        task_id: UUID,
    ) -> list[CheckResult]: ...

    @abstractmethod
    def _accumulate_cost(
        self,
        task_id: UUID,
        task: TaskSpec,
        usage: Usage,
    ) -> None: ...

    @abstractmethod
    async def _send_budget_events(
        self, task_id: UUID,
    ) -> None: ...

    @abstractmethod
    def _validate_output_schema(
        self,
        output: str | None,
        schema_str: str,
    ) -> CheckResult: ...

    @abstractmethod
    def _analyze_structural_advisories(
        self,
        task_ids: list[UUID],
    ) -> list[dict[str, Any]]: ...

    # ------------------------------------------------------------------
    # TaskSchedulerRef protocol methods (from _lifecycle_handlers.py)
    # ------------------------------------------------------------------

    @abstractmethod
    async def handle_start_task(
        self, session_id: str, task_id: str,
    ) -> str: ...

    @abstractmethod
    async def handle_end_task(
        self, session_id: str, message: str,
    ) -> str: ...

    @abstractmethod
    async def handle_create_task(
        self, session_id: str, params: dict[str, Any],
    ) -> str: ...

    @abstractmethod
    async def handle_create_workflow(
        self, session_id: str, params: dict[str, Any],
    ) -> str: ...

    @abstractmethod
    async def handle_create_wait_for(
        self, session_id: str, params: dict[str, Any],
    ) -> str: ...

    @abstractmethod
    async def handle_await_task(
        self, session_id: str, task_id: str,
    ) -> str: ...

    # ------------------------------------------------------------------
    # CheckExecutor protocol methods (from _executor.py)
    # ------------------------------------------------------------------

    @abstractmethod
    async def run_consult(
        self,
        agent: str,
        question: str,
        variable_values: dict[str, str] | None = None,
    ) -> str: ...

    @abstractmethod
    async def run_workflow_check(
        self,
        execution: Execution,
    ) -> CheckResult: ...

    # ------------------------------------------------------------------
    # Static methods called cross-mixin
    # ------------------------------------------------------------------

    @staticmethod
    async def _call_callback(
        callable_path: str,
        context: TaskContext,
    ) -> None:
        import importlib  # noqa: PLC0415
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
