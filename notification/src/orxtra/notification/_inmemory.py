"""Dict-backed in-memory notification backend for tests.

Matches ``PgNotificationBackend`` semantics, including KeyError on
double-ack and cursor-based pagination by UUID comparison.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from orxtra.protocols import NotificationDelivery
from uuid6 import uuid7


class InMemoryNotificationBackend:
    """In-memory notification delivery implementing ``NotificationPort``."""

    def __init__(self) -> None:
        self._deliveries: dict[UUID, NotificationDelivery] = {}

    async def create_delivery(
        self,
        target_principal_id: UUID,
        source_ref: str,
        payload: dict[str, Any],
    ) -> UUID:
        delivery_id = uuid7()
        delivery = NotificationDelivery(
            id=delivery_id,
            target_principal_id=target_principal_id,
            source_ref=source_ref,
            payload=payload,
            created_at=datetime.now(tz=UTC),
            acknowledged_at=None,
        )
        self._deliveries[delivery_id] = delivery
        return delivery_id

    async def list_for_principal(
        self,
        principal_id: UUID,
        *,
        unacknowledged_only: bool = True,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> list[NotificationDelivery]:
        results: list[NotificationDelivery] = []
        # Sort by created_at ASC to match PG ordering.
        sorted_deliveries = sorted(
            self._deliveries.values(),
            key=lambda d: d.created_at,
        )
        for delivery in sorted_deliveries:
            if delivery.target_principal_id != principal_id:
                continue
            if unacknowledged_only and delivery.acknowledged_at is not None:
                continue
            if cursor is not None and delivery.id <= cursor:
                continue
            results.append(delivery)
            if len(results) >= limit:
                break
        return results

    async def acknowledge(self, delivery_id: UUID) -> None:
        existing = self._deliveries.get(delivery_id)
        if existing is None or existing.acknowledged_at is not None:
            msg = (
                f"Notification delivery {delivery_id} not found or"
                " already acknowledged"
            )
            raise KeyError(msg)
        self._deliveries[delivery_id] = NotificationDelivery(
            id=existing.id,
            target_principal_id=existing.target_principal_id,
            source_ref=existing.source_ref,
            payload=existing.payload,
            created_at=existing.created_at,
            acknowledged_at=datetime.now(tz=UTC),
        )

    # -- Expose internals for direct test manipulation --

    def _get_deliveries(self) -> dict[UUID, NotificationDelivery]:
        return self._deliveries
