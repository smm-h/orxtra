from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.protocols._types._auth import (
        AuthContext,
        ConsumerRecord,
        CredentialRecord,
        MacVerdict,
        TrustTier,
    )
    from orxtra.protocols._types._checks import CheckResult
    from orxtra.protocols._types._dispatch import (
        AccumulatorEntry,
        Source,
        Subscription,
        SubscriptionAction,
    )
    from orxtra.protocols._types._events import OverseerEvent
    from orxtra.protocols._types._identity import Principal
    from orxtra.protocols._types._surfaces import SurfaceSpec
    from orxtra.protocols._types._task import Execution
    from orxtra.protocols._types._tool import Tool

T_contra = TypeVar("T_contra", contravariant=True)
T_event_contra = TypeVar("T_event_contra", contravariant=True)


@runtime_checkable
class EventSink(Protocol[T_event_contra]):
    """Receives typed events. Async because some sinks need I/O (PG writes)."""

    async def on_event(self, event: T_event_contra) -> None: ...


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
class SourceStorage(Protocol):
    async def create_source(self, source: Source) -> UUID: ...
    async def get_source(self, source_id: UUID) -> Source | None: ...
    async def get_source_by_slug(self, slug: str) -> Source | None: ...
    async def list_sources(self) -> list[Source]: ...
    async def delete_source(self, source_id: UUID) -> None: ...


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
    SourceStorage, SubscriptionStorage, ActionStorage, AccumulatorStorage, Protocol,
): ...


@runtime_checkable
class EventBus(Protocol):
    """Event notification (replaces LISTEN/NOTIFY)."""

    async def subscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None: ...

    async def unsubscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None: ...

    async def publish(self, channel: str, payload: str) -> None: ...


@runtime_checkable
class SurfaceGenerator(Protocol):
    """Generates A2UI surface specs from model types."""

    def generate(self, model_type: type) -> SurfaceSpec: ...


@runtime_checkable
class CardContributor(Protocol):
    """Contributes a fragment to an A2A Agent Card."""

    def card_fragment(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Auth protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class KeyedMacProvider(Protocol):
    """Non-exportable keyed MAC verification.

    Modeled on KMS: the only operation is verify(). There is no
    get-value or resolve method -- key export is impossible by
    construction. Multiple concurrently-valid key versions enable
    rotation; verdicts report the matched version.
    """

    async def verify(
        self,
        key_ref: str,
        message: bytes,
        signature: str,
        algorithm: str,
    ) -> MacVerdict: ...


@runtime_checkable
class CredentialVerifier(Protocol):
    """Per-credential-type verification strategy.

    Hash verifiers (bearer/api_key) need zero secret capability.
    HMAC verifiers are constructed with a KeyedMacProvider.
    """

    @property
    def credential_type(self) -> str: ...

    async def verify(
        self,
        credential_record: object,
        presented_credential: str,
    ) -> AuthContext: ...


@runtime_checkable
class AuthStorage(Protocol):
    """Storage protocol for auth data, replacing the concrete backend union."""

    async def create_consumer(
        self,
        name: str,
        trust_tier: TrustTier,
        scope_grants: list[str],
        *,
        consumer_id: UUID,
        principal_id: UUID,
    ) -> UUID: ...

    async def get_consumer(
        self,
        consumer_id: UUID,
    ) -> ConsumerRecord | None: ...

    async def disable_consumer(
        self,
        consumer_id: UUID,
    ) -> None: ...

    async def create_credential(
        self,
        consumer_id: UUID,
        credential_type: str,
        raw_value: str,
        *,
        secret_ref: str | None = None,
    ) -> UUID: ...

    async def get_credential_by_id(
        self,
        credential_id: UUID,
    ) -> CredentialRecord | None: ...

    async def get_credential_by_hash(
        self,
        credential_hash: str,
    ) -> CredentialRecord | None: ...

    async def get_credentials_by_consumer(
        self,
        consumer_id: UUID,
        *,
        credential_type: str | None = None,
    ) -> list[CredentialRecord]: ...


# ---------------------------------------------------------------------------
# Identity protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class PrincipalStorage(Protocol):
    """Storage protocol for persisted identity (principals).

    A Principal is the durable identity row for an actor; other tables FK
    to it for attribution and ownership. The identity module owns the
    concrete backend and the domain exceptions referenced below.
    """

    async def mint_principal(
        self,
        kind: str,
        external_ref: UUID,
        display_name: str | None,
    ) -> Principal:
        """Idempotent upsert on ``(kind, external_ref)``.

        Creates the principal if absent; returns the existing row
        otherwise. Never errors on duplicates. This is the crash-safe
        eager-minting primitive: callers can mint unconditionally on every
        actor appearance, and a retry after a partial failure converges to
        the same single row.
        """
        ...

    async def get_principal(self, principal_id: UUID) -> Principal | None: ...

    async def get_principal_by_ref(
        self,
        kind: str,
        external_ref: UUID,
    ) -> Principal | None: ...

    async def list_principals(self, kind: str | None = None) -> list[Principal]: ...

    async def update_display_name(
        self,
        principal_id: UUID,
        display_name: str,
    ) -> None:
        """Set the display name of an existing principal.

        Hard error if the principal is absent -- this is not an upsert.
        """
        ...

    async def delete_principal(self, principal_id: UUID) -> None:
        """Delete a principal.

        Cascades owned subscriptions. Hard domain error if the principal
        has history: events/runs/inbox/sources/consumers FKs are RESTRICT,
        so a principal with any such references cannot be deleted. The
        domain exception is defined by the identity module.
        """
        ...
