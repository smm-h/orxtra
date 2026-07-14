"""Notification CRUD service functions.

Thin wrappers over ``NotificationPort`` that enforce caller scoping:
a caller can only list and acknowledge their own deliveries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.protocols import NotificationDelivery, NotificationPort, Principal


async def list_deliveries(
    notification_port: NotificationPort,
    caller_principal: Principal,
    *,
    unacknowledged_only: bool = True,
    cursor: UUID | None = None,
    limit: int = 50,
) -> list[NotificationDelivery]:
    """List deliveries for the authenticated caller's principal.

    The caller can ONLY list their own deliveries -- the principal is
    derived from the authenticated context, not a user-supplied parameter.
    """
    return await notification_port.list_for_principal(
        caller_principal.id,
        unacknowledged_only=unacknowledged_only,
        cursor=cursor,
        limit=limit,
    )


async def acknowledge_delivery(
    notification_port: NotificationPort,
    caller_principal: Principal,
    *,
    delivery_id: UUID,
) -> None:
    """Acknowledge a delivery, enforcing ownership.

    Fetches the delivery first (via a single-item list query) and
    compares the target_principal_id against the caller. If the
    delivery does not belong to the caller, a hard error is raised.
    """
    # Fetch the delivery to verify ownership. We list with
    # unacknowledged_only=False to find already-acked deliveries too
    # (the ack call will raise KeyError on those, which is the correct
    # behavior -- but the ownership check must work regardless).
    deliveries = await notification_port.list_for_principal(
        caller_principal.id,
        unacknowledged_only=False,
        limit=1000,
    )
    owned_ids = {d.id for d in deliveries}
    if delivery_id not in owned_ids:
        msg = (
            f"Notification delivery {delivery_id} does not belong to "
            f"principal {caller_principal.id}"
        )
        raise PermissionError(msg)

    await notification_port.acknowledge(delivery_id)
