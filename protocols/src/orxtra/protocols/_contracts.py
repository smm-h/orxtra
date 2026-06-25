from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from orxtra.protocols._types._checks import CheckResult
    from orxtra.protocols._types._events import OverseerEvent
    from orxtra.protocols._types._task import Execution
    from orxtra.session import Session

T_contra = TypeVar("T_contra", contravariant=True)


@runtime_checkable
class Renderer(Protocol[T_contra]):
    """Converts a typed result into a text string for the LLM."""

    def render(self, data: T_contra) -> str: ...


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


@runtime_checkable
class OverseerProtocol(Protocol):
    """Protocol for the Overseer, used by the scheduler to avoid
    a direct dependency on the intelligence layer."""

    session: Session

    def prepare_event(self, event: OverseerEvent) -> str: ...


@runtime_checkable
class HealthMonitorProtocol(Protocol):
    """Protocol for health monitoring, used by the scheduler to avoid
    a direct dependency on the intelligence layer."""

    def is_degraded(self, event_type: str) -> bool: ...

    def record_event(
        self, event_type: str, *, success: bool, is_repetition: bool = False,
    ) -> None: ...
