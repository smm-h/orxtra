from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType

_ALL_SENTINEL = "__all__"


class AutonomyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"

    _RULES: MappingProxyType[str, frozenset[str]]  # type: ignore[assignment]

    def is_autonomous(self, action_type: str) -> bool:
        allowed = self._RULES[self.value]
        if _ALL_SENTINEL in allowed:
            return True
        return action_type in allowed

    def requires_approval(self, action_type: str) -> bool:
        return not self.is_autonomous(action_type)


# Assigned after class body because StrEnum members
# occupy the namespace during class definition.
AutonomyLevel._RULES = MappingProxyType({  # type: ignore[attr-defined]
    "low": frozenset({"read_only"}),
    "medium": frozenset({
        "read_only",
        "retry",
        "budget_reallocation",
        "concurrency",
        "task_assumption",
    }),
    "high": frozenset({
        "read_only",
        "retry",
        "budget_reallocation",
        "concurrency",
        "task_assumption",
        "scope_change",
        "architecture_decision",
        "understanding_assumption",
    }),
    "max": frozenset({_ALL_SENTINEL}),
})
