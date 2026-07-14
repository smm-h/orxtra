"""Tests for the notification SSE stream endpoint (Phase 6.2).

Covers:
- Authenticated GET returns text/event-stream content type.
- Different principals see different streams.
- Unauthenticated request is rejected (401).
- SYSTEM-tier callers can stream another principal's notifications.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface
from fastware.testing import AsyncTestClient
from orxtra.a2a._skills import SkillRegistry
from orxtra.api._compositor import CompositorConfig, create_compositor
from orxtra.auth import (
    Authenticator,
    HashCredentialVerifier,
    HmacCredentialVerifier,
    InMemoryAuthBackend,
)
from orxtra.identity import InMemoryPrincipalStorage
from orxtra.notification import InMemoryNotificationBackend
from orxtra.protocols import (
    KIND_CONSUMER,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    TrustTier,
)
from orxtra.services import DispatchContext
from orxtra.trace import InMemoryEventBus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_card() -> AgentCard:
    return AgentCard(
        name="test-agent",
        description="Test agent",
        version="0.0.1",
        supported_interfaces=[
            AgentInterface(url="http://localhost:8080/a2a"),
        ],
        capabilities=AgentCapabilities(streaming=True),
    )


@pytest.fixture
def skill_registry() -> SkillRegistry:
    return SkillRegistry([])


@pytest.fixture
def notification_backend() -> InMemoryNotificationBackend:
    return InMemoryNotificationBackend()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def principal_storage() -> InMemoryPrincipalStorage:
    return InMemoryPrincipalStorage()


@pytest.fixture
def auth_backend() -> InMemoryAuthBackend:
    return InMemoryAuthBackend()


@pytest.fixture
def authenticator(auth_backend: InMemoryAuthBackend) -> Authenticator:
    verifiers: dict[str, HashCredentialVerifier | HmacCredentialVerifier] = {
        "bearer": HashCredentialVerifier("bearer", auth_backend),
    }
    return Authenticator(auth_backend, verifiers)


async def _register_consumer(
    auth_backend: InMemoryAuthBackend,
    principal_storage: InMemoryPrincipalStorage,
    token: str,
    name: str,
    trust_tier: TrustTier = TrustTier.VERIFIED,
) -> Any:
    """Register a consumer with a bearer credential and matching principal.

    Returns the Principal object for the registered consumer.
    """
    consumer_id = await auth_backend.create_consumer(
        name,
        trust_tier,
        ["api"],
        consumer_id=uuid4(),
        principal_id=uuid4(),
    )
    await auth_backend.create_credential(consumer_id, "bearer", token)

    # Mint a principal for this consumer (resolve_caller_principal looks
    # up by KIND_CONSUMER + consumer_id as external_ref).
    return await principal_storage.mint_principal(
        KIND_CONSUMER, consumer_id, name,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNotificationStreamAuth:
    """Authentication and authorization for the notification SSE endpoint."""

    async def test_unauthenticated_request_rejected(
        self,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
        notification_backend: InMemoryNotificationBackend,
        event_bus: InMemoryEventBus,
        principal_storage: InMemoryPrincipalStorage,
        authenticator: Authenticator,
    ) -> None:
        """Anonymous GET to /notifications/stream returns 401."""
        ctx = DispatchContext(
            notification_port=notification_backend,
            event_bus=event_bus,
            principal_storage=principal_storage,
        )
        config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
        )
        app = create_compositor(config)
        async with AsyncTestClient(app) as client:
            resp = await client.get("/notifications/stream")
            assert resp.status_code == 401

    async def test_authenticated_get_returns_event_stream(
        self,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
        notification_backend: InMemoryNotificationBackend,
        event_bus: InMemoryEventBus,
        principal_storage: InMemoryPrincipalStorage,
        auth_backend: InMemoryAuthBackend,
        authenticator: Authenticator,
    ) -> None:
        """Authenticated GET returns text/event-stream content type."""
        principal = await _register_consumer(
            auth_backend, principal_storage, "tok-1", "user-1",
        )
        # Create a delivery so the stream has something to replay.
        await notification_backend.create_delivery(
            principal.id, "test-src", {"msg": "hello"},
        )

        ctx = DispatchContext(
            notification_port=notification_backend,
            event_bus=event_bus,
            principal_storage=principal_storage,
        )
        config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
        )
        app = create_compositor(config)

        async with (
            AsyncTestClient(app) as client,
            asyncio.timeout(5),
            client.stream(
                "GET",
                "/notifications/stream",
                headers={"authorization": "Bearer tok-1"},
            ) as resp,
        ):
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get(
                "content-type", "",
            )
            # Read the first SSE event from the replay.
            lines: list[str] = []
            async for raw_line in resp.aiter_lines():
                lines.append(raw_line)
                # SSE events end with a double newline; aiter_lines
                # strips trailing newlines, so an empty line marks
                # the event boundary.
                if raw_line == "":
                    break

            # Verify the SSE event format: id, event, data lines.
            text = "\n".join(lines)
            assert "event: notification" in text
            assert '"msg": "hello"' in text


class TestNotificationStreamIsolation:
    """Different principals see only their own notifications."""

    async def test_principals_see_own_deliveries_only(
        self,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
        notification_backend: InMemoryNotificationBackend,
        event_bus: InMemoryEventBus,
        principal_storage: InMemoryPrincipalStorage,
        auth_backend: InMemoryAuthBackend,
        authenticator: Authenticator,
    ) -> None:
        """Each principal's SSE stream contains only its own deliveries."""
        p1 = await _register_consumer(
            auth_backend, principal_storage, "tok-p1", "user-p1",
        )
        p2 = await _register_consumer(
            auth_backend, principal_storage, "tok-p2", "user-p2",
        )

        await notification_backend.create_delivery(
            p1.id, "src-a", {"for": "p1"},
        )
        await notification_backend.create_delivery(
            p2.id, "src-b", {"for": "p2"},
        )

        ctx = DispatchContext(
            notification_port=notification_backend,
            event_bus=event_bus,
            principal_storage=principal_storage,
        )
        config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
        )
        app = create_compositor(config)

        async def _read_first_event(
            client: Any, token: str,
        ) -> str:
            async with asyncio.timeout(5), client.stream(
                "GET",
                "/notifications/stream",
                headers={"authorization": f"Bearer {token}"},
            ) as resp:
                assert resp.status_code == 200
                lines: list[str] = []
                async for raw_line in resp.aiter_lines():
                    lines.append(raw_line)
                    if raw_line == "":
                        break
                return "\n".join(lines)

        async with AsyncTestClient(app) as client:
            p1_event = await _read_first_event(client, "tok-p1")
            p2_event = await _read_first_event(client, "tok-p2")

        assert '"for": "p1"' in p1_event
        assert '"for": "p2"' not in p1_event

        assert '"for": "p2"' in p2_event
        assert '"for": "p1"' not in p2_event


class TestSystemTierOverride:
    """SYSTEM-tier callers can stream another principal's notifications."""

    async def test_system_can_stream_other_principal(
        self,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
        notification_backend: InMemoryNotificationBackend,
        event_bus: InMemoryEventBus,
        principal_storage: InMemoryPrincipalStorage,
        auth_backend: InMemoryAuthBackend,
        authenticator: Authenticator,
    ) -> None:
        """A SYSTEM-tier caller with ?principal_id= streams that principal."""
        # Seed the system principal.
        await principal_storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )

        # Register a SYSTEM-tier consumer.
        system_consumer_id = await auth_backend.create_consumer(
            "system-user",
            TrustTier.SYSTEM,
            ["api"],
            consumer_id=uuid4(),
            principal_id=uuid4(),
        )
        await auth_backend.create_credential(
            system_consumer_id, "bearer", "system-tok",
        )

        # Register a target principal.
        target = await _register_consumer(
            auth_backend, principal_storage, "target-tok", "target-user",
        )

        # Create a delivery for the target.
        await notification_backend.create_delivery(
            target.id, "src-target", {"msg": "secret"},
        )

        ctx = DispatchContext(
            notification_port=notification_backend,
            event_bus=event_bus,
            principal_storage=principal_storage,
        )
        config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
        )
        app = create_compositor(config)

        async with (
            AsyncTestClient(app) as client,
            asyncio.timeout(5),
            client.stream(
                "GET",
                "/notifications/stream",
                params={"principal_id": str(target.id)},
                headers={"authorization": "Bearer system-tok"},
            ) as resp,
        ):
            assert resp.status_code == 200
            lines: list[str] = []
            async for raw_line in resp.aiter_lines():
                lines.append(raw_line)
                if raw_line == "":
                    break
            text = "\n".join(lines)
            assert '"msg": "secret"' in text

    async def test_non_system_cannot_stream_other_principal(
        self,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
        notification_backend: InMemoryNotificationBackend,
        event_bus: InMemoryEventBus,
        principal_storage: InMemoryPrincipalStorage,
        auth_backend: InMemoryAuthBackend,
        authenticator: Authenticator,
    ) -> None:
        """A non-SYSTEM caller passing a different principal_id gets 403."""
        await _register_consumer(
            auth_backend, principal_storage, "tok-p1", "user-p1",
        )
        other_principal_id = uuid4()

        ctx = DispatchContext(
            notification_port=notification_backend,
            event_bus=event_bus,
            principal_storage=principal_storage,
        )
        config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
        )
        app = create_compositor(config)

        async with AsyncTestClient(app) as client:
            resp = await client.get(
                "/notifications/stream",
                params={"principal_id": str(other_principal_id)},
                headers={"authorization": "Bearer tok-p1"},
            )
            assert resp.status_code == 403
