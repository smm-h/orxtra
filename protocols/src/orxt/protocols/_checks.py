from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import uuid

    from orxt.protocols._execution import CheckResult
    from orxt.protocols._task import Execution


@dataclass(frozen=True)
class CheckContext:
    variables: dict[str, Any]
    agent_output: str | None
    run_id: uuid.UUID
    session_id: str | None
    task_name: str
    task_id: uuid.UUID
    attempt: int
    parent_task_id: uuid.UUID | None


@dataclass(frozen=True)
class CheckAgentContext:
    task: str
    agent_output: str
    mechanical_results: str
    task_name: str
    attempt: int
    notepad: str


class CheckExecutor(Protocol):
    async def run_consult(
        self,
        agent: str,
        question: str,
        variable_values: dict[str, str] | None = None,
    ) -> str: ...

    async def run_workflow_check(
        self,
        execution: Execution,
    ) -> CheckResult: ...


OnSuccessCallback = Awaitable[None]
PreRetryCallback = Awaitable[None]
