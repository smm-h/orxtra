from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.protocols._types._checks import CheckResult
    from orxtra.protocols._types._dispatch import (
        AccumulatorEntry,
        Subscription,
        SubscriptionAction,
    )
    from orxtra.protocols._types._events import OverseerEvent
    from orxtra.protocols._types._task import Execution
    from orxtra.protocols._types._tool import Tool

T_contra = TypeVar("T_contra", contravariant=True)


@runtime_checkable
class ActionExecutor(Protocol):
    """Injected executor for WorkflowAction dispatch.

    The dispatch module cannot start workflows directly (that would
    create a downward dependency to the scheduler). Callers inject an
    executor that bridges the gap.
    """

    async def execute_workflow(
        self,
        workflow_path: str,
        config: dict[str, object],
        events: list[dict[str, object]],
    ) -> None: ...


# Callback type for EventAction: fire a new event back into the delivery
# engine without creating a circular import.
type EventFireCallback = Callable[
    [str, dict[str, object] | None],
    Awaitable[None],
]


@runtime_checkable
class Renderer(Protocol[T_contra]):
    """Converts a typed result into a text string for the LLM."""

    def render(self, data: T_contra) -> str: ...


@runtime_checkable
class SessionProtocol(Protocol):
    """Structural protocol for the Session, used to break the
    dependency from protocols to session."""

    @property
    def tools(self) -> list[Tool]: ...

    def update_tools(self, tools: list[Tool]) -> None: ...

    def send(self, message: str) -> AsyncIterator[Any]: ...


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

    session: SessionProtocol

    def prepare_event(self, event: OverseerEvent) -> str: ...


@runtime_checkable
class HealthMonitorProtocol(Protocol):
    """Protocol for health monitoring, used by the scheduler to avoid
    a direct dependency on the intelligence layer."""

    def is_degraded(self, event_type: str) -> bool: ...

    def record_event(
        self, event_type: str, *, success: bool, is_repetition: bool = False,
    ) -> None: ...


@runtime_checkable
class EventDelivery(Protocol):
    """Protocol for event delivery (fire-and-wait).

    Used by the scheduler so callers can inject alternative
    implementations (e.g. PG-backed LISTEN/NOTIFY).
    """

    async def fire(
        self,
        event_name: str,
        payload: dict[str, object] | None = None,
        *,
        source: str | None = None,
    ) -> None: ...

    async def wait_for(
        self,
        event_name: str,
        *,
        deadline_seconds: float,
    ) -> dict[str, object] | None: ...


@runtime_checkable
class FlushScheduler(Protocol):
    """Schedules deferred flush callbacks with a deadline.

    Used by the write-safety module to schedule and cancel
    flush operations without depending on a concrete scheduler.
    """

    def schedule_flush(
        self,
        deadline: float,
        callback: Callable[[], Awaitable[None]],
    ) -> object: ...  # returns a handle for cancellation

    def cancel_flush(self, handle: object) -> None: ...


@runtime_checkable
class SubscriptionStorage(Protocol):
    async def create_subscription(self, subscription: Subscription) -> UUID: ...
    async def get_subscription(self, sub_id: UUID) -> Subscription | None: ...
    async def list_subscriptions(
        self, *, enabled_only: bool = True,
    ) -> list[Subscription]: ...
    async def update_subscription(
        self, sub_id: UUID, *, enabled: bool,
    ) -> None: ...
    async def delete_subscription(self, sub_id: UUID) -> None: ...


@runtime_checkable
class ActionStorage(Protocol):
    async def create_action(self, action: SubscriptionAction) -> UUID: ...
    async def list_actions(self, sub_id: UUID) -> list[SubscriptionAction]: ...
    async def delete_actions(self, sub_id: UUID) -> None: ...


@runtime_checkable
class AccumulatorStorage(Protocol):
    async def buffer_event(self, entry: AccumulatorEntry) -> UUID: ...
    async def claim_batch(
        self, action_id: UUID, limit: int = 100,
    ) -> list[AccumulatorEntry]: ...
    async def confirm_batch(self, entry_ids: list[UUID]) -> None: ...
    async def pending_count(self, action_id: UUID) -> int: ...


@runtime_checkable
class DispatchBackend(
    SubscriptionStorage, ActionStorage, AccumulatorStorage, Protocol,
): ...


@runtime_checkable
class EventBus(Protocol):
    """Event notification (replaces LISTEN/NOTIFY)."""

    async def subscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None: ...

    async def publish(self, channel: str, payload: str) -> None: ...
