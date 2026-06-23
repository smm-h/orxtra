from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


def _paths_overlap(a: str, b: str) -> bool:
    """Check if two paths overlap (exact match or prefix containment).

    Two paths overlap when either is a prefix of the other. A task writing
    to ``/src/a/`` conflicts with a task writing to ``/src/a/b/`` because
    the first task's scope includes the second's files.
    """
    if a == b:
        return True
    # Normalize with trailing separator for prefix comparison so that
    # "/src/a" does not falsely match "/src/abc".
    a_prefix = a if a.endswith(os.sep) else a + os.sep
    b_prefix = b if b.endswith(os.sep) else b + os.sep
    return b.startswith(a_prefix) or a.startswith(b_prefix)


class FileLockRegistry:
    def __init__(self) -> None:
        self._claims: dict[UUID, set[str]] = {}

    def claim(self, workflow_id: UUID, paths: list[str]) -> None:
        conflict = self.check_conflict(paths)
        if conflict is not None:
            msg = f"Paths conflict with workflow {conflict}"
            raise ValueError(msg)
        self._claims[workflow_id] = set(paths)

    def release(self, workflow_id: UUID) -> None:
        self._claims.pop(workflow_id, None)

    def check_conflict(self, paths: list[str]) -> UUID | None:
        for wf_id, claimed in self._claims.items():
            for new_path in paths:
                for claimed_path in claimed:
                    if _paths_overlap(new_path, claimed_path):
                        return wf_id
        return None
