from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


class PrincipalInUseError(Exception):
    """Raised when a principal cannot be deleted because it is still referenced.

    A principal that any other row points at (events, runs, inbox, sources,
    consumers -- all RESTRICT foreign keys) is undeletable: it anchors durable
    history and attribution. Deletion is reserved for principals that were
    minted but never referenced.

    The right way to retire an actor with history is to deactivate it on the
    consuming side (e.g. disable the consumer, archive the source), not to
    delete its identity row.

    Until the referencing FKs land in a later phase, no table points at
    ``principals`` yet, so deletes of unreferenced principals succeed silently
    and this error is not yet raised in practice.
    """

    def __init__(self, principal_id: UUID) -> None:
        self.principal_id = principal_id
        super().__init__(
            f"Principal {principal_id} is referenced by existing history or "
            f"linkage and cannot be deleted. Actors with history are "
            f"undeletable -- deactivate the actor on the consuming side "
            f"(disable the consumer, archive the source, etc.) instead.",
        )
