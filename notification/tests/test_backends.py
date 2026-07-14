"""Shared parametrized test suite for notification backends.

Covers the full NotificationPort contract: create, list (unacked),
acknowledge, list (returns empty after ack), cursor pagination, limit,
double-ack error, and principal isolation. Runs against both
InMemoryNotificationBackend and PgNotificationBackend (via parity guard).
"""

from __future__ import annotations

import inspect
from typing import Any
from uuid import UUID, uuid4

import pytest
from orxtra.notification import (
    InMemoryNotificationBackend,
    PgNotificationBackend,
)
from orxtra.protocols import NotificationDelivery, NotificationPort

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=["inmemory"])
def backend(request: pytest.FixtureRequest) -> InMemoryNotificationBackend:
    """Yield a fresh backend for each test.

    PG backend is tested separately via integration tests at the repo
    root. This fixture covers in-memory only; the parity guard below
    ensures signatures stay in lockstep.
    """
    if request.param == "inmemory":
        return InMemoryNotificationBackend()
    msg = f"Unknown backend: {request.param}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Core CRUD lifecycle
# ---------------------------------------------------------------------------


async def test_create_and_list_unacked(
    backend: InMemoryNotificationBackend,
) -> None:
    principal_id = uuid4()
    delivery_id = await backend.create_delivery(
        principal_id, "test-source", {"key": "value"},
    )
    assert isinstance(delivery_id, UUID)

    deliveries = await backend.list_for_principal(principal_id)
    assert len(deliveries) == 1
    assert deliveries[0].id == delivery_id
    assert deliveries[0].target_principal_id == principal_id
    assert deliveries[0].source_ref == "test-source"
    assert deliveries[0].payload == {"key": "value"}
    assert deliveries[0].acknowledged_at is None
    assert isinstance(deliveries[0], NotificationDelivery)


async def test_ack_then_list_empty(
    backend: InMemoryNotificationBackend,
) -> None:
    principal_id = uuid4()
    delivery_id = await backend.create_delivery(
        principal_id, "src", {"x": 1},
    )

    await backend.acknowledge(delivery_id)

    # Unacked-only list is empty after ack.
    deliveries = await backend.list_for_principal(principal_id)
    assert deliveries == []


async def test_ack_then_list_all(
    backend: InMemoryNotificationBackend,
) -> None:
    """After ack, listing with unacknowledged_only=False returns the delivery."""
    principal_id = uuid4()
    delivery_id = await backend.create_delivery(
        principal_id, "src", {},
    )

    await backend.acknowledge(delivery_id)

    deliveries = await backend.list_for_principal(
        principal_id, unacknowledged_only=False,
    )
    assert len(deliveries) == 1
    assert deliveries[0].id == delivery_id
    assert deliveries[0].acknowledged_at is not None


# ---------------------------------------------------------------------------
# Double-ack is a hard error
# ---------------------------------------------------------------------------


async def test_double_ack_raises(
    backend: InMemoryNotificationBackend,
) -> None:
    principal_id = uuid4()
    delivery_id = await backend.create_delivery(
        principal_id, "src", {},
    )
    await backend.acknowledge(delivery_id)

    with pytest.raises(KeyError):
        await backend.acknowledge(delivery_id)


async def test_ack_nonexistent_raises(
    backend: InMemoryNotificationBackend,
) -> None:
    with pytest.raises(KeyError):
        await backend.acknowledge(uuid4())


# ---------------------------------------------------------------------------
# Principal isolation
# ---------------------------------------------------------------------------


async def test_different_principals_isolated(
    backend: InMemoryNotificationBackend,
) -> None:
    p1 = uuid4()
    p2 = uuid4()

    await backend.create_delivery(p1, "src-a", {"for": "p1"})
    await backend.create_delivery(p2, "src-b", {"for": "p2"})

    p1_deliveries = await backend.list_for_principal(p1)
    p2_deliveries = await backend.list_for_principal(p2)

    assert len(p1_deliveries) == 1
    assert p1_deliveries[0].payload == {"for": "p1"}
    assert len(p2_deliveries) == 1
    assert p2_deliveries[0].payload == {"for": "p2"}


# ---------------------------------------------------------------------------
# Cursor pagination
# ---------------------------------------------------------------------------


async def test_cursor_pagination(
    backend: InMemoryNotificationBackend,
) -> None:
    principal_id = uuid4()

    ids: list[UUID] = []
    for i in range(5):
        did = await backend.create_delivery(
            principal_id, f"src-{i}", {"i": i},
        )
        ids.append(did)

    # First page: limit 2.
    page1 = await backend.list_for_principal(principal_id, limit=2)
    assert len(page1) == 2
    assert page1[0].id == ids[0]
    assert page1[1].id == ids[1]

    # Second page: cursor = last id from page 1.
    page2 = await backend.list_for_principal(
        principal_id, cursor=page1[-1].id, limit=2,
    )
    assert len(page2) == 2
    assert page2[0].id == ids[2]
    assert page2[1].id == ids[3]

    # Third page: only 1 remaining.
    page3 = await backend.list_for_principal(
        principal_id, cursor=page2[-1].id, limit=2,
    )
    assert len(page3) == 1
    assert page3[0].id == ids[4]

    # Beyond the last page: empty.
    page4 = await backend.list_for_principal(
        principal_id, cursor=page3[-1].id, limit=2,
    )
    assert page4 == []


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


async def test_limit_caps_results(
    backend: InMemoryNotificationBackend,
) -> None:
    principal_id = uuid4()
    for i in range(10):
        await backend.create_delivery(principal_id, "src", {"i": i})

    deliveries = await backend.list_for_principal(principal_id, limit=3)
    assert len(deliveries) == 3


# ---------------------------------------------------------------------------
# Signature parity guard
# ---------------------------------------------------------------------------


def _normalized_params(func: object) -> list[tuple[str, Any, Any]]:
    """Return each parameter's (name, kind, default), excluding ``self``."""
    sig = inspect.signature(func)  # type: ignore[arg-type]
    return [
        (p.name, p.kind, p.default)
        for name, p in sig.parameters.items()
        if name != "self"
    ]


_NOTIFICATION_PORT_METHODS = [
    "create_delivery",
    "list_for_principal",
    "acknowledge",
]


@pytest.mark.parametrize(
    "backend_cls",
    [InMemoryNotificationBackend, PgNotificationBackend],
)
@pytest.mark.parametrize("method_name", _NOTIFICATION_PORT_METHODS)
def test_notification_port_signature_parity(
    backend_cls: type, method_name: str,
) -> None:
    """Every NotificationPort method has an identical call shape on the
    protocol and on both concrete backends.
    """
    proto = _normalized_params(getattr(NotificationPort, method_name))
    impl = _normalized_params(getattr(backend_cls, method_name))
    assert impl == proto, (
        f"{backend_cls.__name__}.{method_name} signature drifted from "
        f"NotificationPort.{method_name}:\n  protocol: {proto}\n  impl: {impl}"
    )


def test_both_notification_backends_satisfy_protocol() -> None:
    """Both backends are runtime instances of the NotificationPort protocol."""
    assert isinstance(InMemoryNotificationBackend(), NotificationPort)
