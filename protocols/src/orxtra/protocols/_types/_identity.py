from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    """A durable identity row; one per actor in the system.

    Every actor -- a run, an API consumer, a webhook source, the system
    itself, or an app-registered kind (e.g. users) -- gets exactly one
    principals row. Other tables FK to it for attribution and ownership.

    This is NOT the per-request authentication context (that is
    ``AuthContext``, which is ephemeral and never persisted). A Principal
    is the stable, persisted identity that an ``AuthContext`` may resolve
    to across many requests.
    """

    id: UUID
    kind: str
    external_ref: UUID
    display_name: str | None
    created_at: datetime


# -- Built-in principal kinds --
# Kinds owned by the framework. Apps may register additional kinds
# (e.g. "user") that are not part of this set.

KIND_RUN = "run"
KIND_CONSUMER = "consumer"
KIND_SOURCE = "source"
KIND_SYSTEM = "system"

BUILTIN_KINDS: frozenset[str] = frozenset({
    KIND_RUN,
    KIND_CONSUMER,
    KIND_SOURCE,
    KIND_SYSTEM,
})

# The system principal is a singleton. Its principals row still needs a
# value in the NOT NULL UNIQUE external_ref column, so we use the
# all-zeros UUID as a schema-level sentinel. It is never used as a real
# foreign reference -- no row in any other table is identified by it.
SYSTEM_PRINCIPAL_EXTERNAL_REF: UUID = UUID(int=0)
