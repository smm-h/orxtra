from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


class PrincipalInUseError(Exception):
    """Raised when a principal cannot be deleted because it is still referenced.

    A principal that any durable-history row points at is undeletable: it
    anchors attribution. The referencing RESTRICT foreign keys are:

    - ``events.principal_id`` (the actor that emitted the event)
    - ``runs.created_by`` (the run's creator)
    - ``sources.created_by`` (the source's creator)
    - ``inbox_items.resolved_by`` (the actor that resolved the item)
    - ``consumers.principal_id`` (a consumer's own backing principal)

    Deletion is reserved for principals that were minted but never referenced
    by history. Note that ``subscriptions.principal_id`` is the sole CASCADE
    referent -- a subscription is operational state that is *deleted with* its
    owner rather than blocking the delete, so it never triggers this error.

    The right way to retire an actor with history is to deactivate it on the
    consuming side (e.g. disable the consumer, archive the source), not to
    delete its identity row.
    """

    def __init__(self, principal_id: UUID) -> None:
        self.principal_id = principal_id
        super().__init__(
            f"Principal {principal_id} is referenced by existing history or "
            f"linkage and cannot be deleted. Actors with history are "
            f"undeletable -- deactivate the actor on the consuming side "
            f"(disable the consumer, archive the source, etc.) instead.",
        )
