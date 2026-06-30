"""State snapshot and delta computation for AG-UI."""

from __future__ import annotations

import time
from typing import Any

import jsonpatch  # type: ignore[import-untyped]
from ag_ui.core import (
    StateDeltaEvent,
    StateSnapshotEvent,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class StateManager:
    """Builds AG-UI state snapshots and computes RFC 6902 JSON Patch deltas.

    The snapshot queries run, task, inbox, and constraint data from the
    services layer (via provided query functions) and assembles them into
    a state dict suitable for ``StateSnapshotEvent``.
    """

    def __init__(self) -> None:
        self._last_state: dict[str, Any] | None = None

    async def snapshot(
        self,
        *,
        run_id: str,
        query_run: Any = None,
        query_tasks: Any = None,
        query_inbox: Any = None,
        query_constraints: Any = None,
    ) -> StateSnapshotEvent:
        """Build a StateSnapshotEvent from the current run state.

        Query functions are optional async callables that accept ``run_id``
        and return dicts. When not provided, the corresponding section is
        omitted from the snapshot.
        """
        state: dict[str, Any] = {"run_id": run_id}

        if query_run is not None:
            state["run"] = await query_run(run_id)
        if query_tasks is not None:
            state["tasks"] = await query_tasks(run_id)
        if query_inbox is not None:
            state["inbox"] = await query_inbox(run_id)
        if query_constraints is not None:
            state["constraints"] = await query_constraints(run_id)

        self._last_state = state

        return StateSnapshotEvent(
            snapshot=state,
            timestamp=_now_ms(),
        )

    def compute_delta(
        self,
        old_state: dict[str, Any],
        new_state: dict[str, Any],
    ) -> StateDeltaEvent | None:
        """Compute an RFC 6902 JSON Patch between two state dicts.

        Returns None if the states are identical.
        """
        patch = jsonpatch.make_patch(old_state, new_state)
        patch_list: list[Any] = patch.patch
        if not patch_list:
            return None

        self._last_state = new_state

        return StateDeltaEvent(
            delta=patch_list,
            timestamp=_now_ms(),
        )

    @property
    def last_state(self) -> dict[str, Any] | None:
        """The most recently captured state, or None if no snapshot taken."""
        return self._last_state
