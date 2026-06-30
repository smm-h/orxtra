"""Stateful translator between orxtra TaskState and A2A task states."""

from __future__ import annotations

from dataclasses import dataclass

from orxtra.protocols import TaskState

from a2a.types.a2a_pb2 import (
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_SUBMITTED,
    TASK_STATE_WORKING,
)

# Sentinel for "buffered" -- the bridge swallows the event and waits
# for the next state transition (retry or escalation).
_BUFFERED = None

# Sentinel for unmapped states (distinct from _BUFFERED/None).
_UNMAPPED = object()


@dataclass
class TranslationResult:
    """Result of a state translation."""

    a2a_state: int | None
    extension_metadata: dict[str, str]


class TaskStateBridge:
    """Stateful translator between orxtra TaskState and A2A.

    The mapping is exhaustive -- every TaskState value is handled
    explicitly. If a new value is added to the enum and not handled
    here, translate() raises a ValueError.

    POSTCHECK_FAILED is buffered: the bridge returns None and waits
    for the next state transition (retry -> ACTIVE, or escalation
    -> ESCALATED).
    """

    def translate(
        self, orxtra_state: TaskState,
    ) -> TranslationResult:
        """Translate an orxtra TaskState to an A2A task state.

        Returns TranslationResult with a2a_state set to the
        corresponding A2A state integer, or None if buffered.

        Raises ValueError if the orxtra_state is not recognized.
        """
        extension = {"orxtra:sub_state": orxtra_state.value}

        a2a_state = _MAP.get(orxtra_state, _UNMAPPED)
        if a2a_state is _UNMAPPED:
            msg = f"Unhandled TaskState: {orxtra_state!r}"
            raise ValueError(msg)

        # a2a_state is int | None at this point (None = buffered)
        return TranslationResult(
            a2a_state=a2a_state,  # type: ignore[arg-type]
            extension_metadata=extension,
        )


# Exhaustive mapping from orxtra TaskState to A2A task state int.
# None means "buffered" -- don't emit, wait for next transition.
_MAP: dict[TaskState, int | None] = {
    TaskState.CREATED: TASK_STATE_SUBMITTED,
    TaskState.PRECHECKING: TASK_STATE_WORKING,
    TaskState.ACTIVE: TASK_STATE_WORKING,
    TaskState.SUSPENDED: TASK_STATE_WORKING,
    TaskState.POSTCHECKING: TASK_STATE_WORKING,
    TaskState.COMPLETED: TASK_STATE_COMPLETED,
    TaskState.PRECHECK_FAILED: TASK_STATE_FAILED,
    TaskState.POSTCHECK_FAILED: _BUFFERED,
    TaskState.ESCALATED: TASK_STATE_FAILED,
    TaskState.CANCELLED: TASK_STATE_CANCELED,
}


def _verify_exhaustive() -> None:
    """Verify at import time that every TaskState member is mapped."""
    for member in TaskState:
        if member not in _MAP:
            msg = (
                f"TaskState.{member.name} is not mapped in "
                f"_MAP. Add it to ensure exhaustive coverage."
            )
            raise RuntimeError(msg)


_verify_exhaustive()
