from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class NotificationDelivery:
    """A single notification delivery addressed to a principal.

    Frozen dataclass matching the ``notification_deliveries`` table schema.
    Used as the return element of ``NotificationPort.list_for_principal``.
    """

    id: UUID
    target_principal_id: UUID
    source_ref: str
    payload: dict[str, Any]
    created_at: datetime
    acknowledged_at: datetime | None
