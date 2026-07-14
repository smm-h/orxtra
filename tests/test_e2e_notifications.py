"""E2E proof of the notification delivery pipeline.

Full cycle against real PostgreSQL (via testcontainers):
1. Create a consumer principal with self-subscription
   (notification_event_types=["test_event"]).
2. Fire a matching event (event_type="test_event",
   principal_id=the consumer's principal).
3. The dispatch worker processes it -- the NotifyAction writes a delivery.
4. List deliveries for the principal -- the delivery appears.
5. Acknowledge it -- it disappears from the unacked list.

This proves the end-to-end notification pipeline: principal creation with
self-subscription wiring, event firing, dispatch worker processing with
NotifyAction execution, notification delivery listing, and acknowledgement.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from uuid import UUID
import uuid6
from orxtra.dispatch import PgDispatchBackend
from orxtra.identity import KindRegistry, PgPrincipalStorage
from orxtra.notification import PgNotificationBackend
from orxtra.protocols import (
    KIND_CONSUMER,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
)
from orxtra.services import create_dispatch_worker
from orxtra.services._identity import create_principal
from orxtra.trace import TraceWriter

from tests.pg_fixtures import skip_no_docker

pytestmark = [skip_no_docker, pytest.mark.timeout(60)]


async def _seed_system(pool: Any) -> UUID:
    """Idempotently seed the system principal, return its id."""
    storage = PgPrincipalStorage(pool)
    principal = await storage.mint_principal(
        KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
    )
    return principal.id


async def test_notification_delivery_full_cycle(
    pg_pool: Any,
) -> None:
    """Full notification delivery pipeline.

    principal -> event -> dispatch -> delivery -> list -> ack -> gone from
    unacked list.
    """
    await _seed_system(pg_pool)
    storage = PgPrincipalStorage(pg_pool)
    backend = PgDispatchBackend(pg_pool)
    notification_backend = PgNotificationBackend(pg_pool)
    kind_registry = KindRegistry()

    # Step 1: Create a consumer principal with self-subscription.
    consumer = await create_principal(
        backend,
        storage,
        kind_registry,
        kind=KIND_CONSUMER,
        external_ref=uuid6.uuid7(),
        display_name="notif-consumer",
        notification_event_types=["test_event"],
    )
    principal_id = consumer.id

    # Step 2: Fire a matching event attributed to the consumer.
    writer = TraceWriter(pg_pool)
    _event_id, inserted = await writer.write_event(
        None,
        "test_event",
        {"msg": "hello notification"},
        principal_id=principal_id,
    )
    assert inserted, "the event must have been inserted"

    # Step 3: Run the dispatch worker with a notification port.
    worker = await create_dispatch_worker(
        pg_pool,
        notification_port=notification_backend,
        cursor_name="notif-e2e-cursor",
        poll_interval=0.1,
    )
    run_task = asyncio.create_task(worker.run())
    await asyncio.sleep(1.0)
    await worker.stop()
    await run_task

    # Step 4: List deliveries for the principal -- the delivery appears.
    deliveries = await notification_backend.list_for_principal(principal_id)
    assert len(deliveries) >= 1, (
        "at least one notification delivery must exist after the dispatch "
        "worker processes the matching event"
    )
    delivery = deliveries[0]
    assert delivery.target_principal_id == principal_id
    assert delivery.source_ref == "self-subscription"
    assert delivery.acknowledged_at is None

    # Step 5: Acknowledge it -- it disappears from the unacked list.
    await notification_backend.acknowledge(delivery.id)

    unacked = await notification_backend.list_for_principal(principal_id)
    assert len(unacked) == 0, (
        "no unacked deliveries should remain after acknowledgement"
    )

    # Verify it still exists in the full list (all, not just unacked).
    all_deliveries = await notification_backend.list_for_principal(
        principal_id, unacknowledged_only=False,
    )
    assert len(all_deliveries) >= 1
    acked = [d for d in all_deliveries if d.id == delivery.id]
    assert len(acked) == 1
    assert acked[0].acknowledged_at is not None
