from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


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
        path_set = set(paths)
        for wf_id, claimed in self._claims.items():
            if claimed & path_set:
                return wf_id
        return None
