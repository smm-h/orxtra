from __future__ import annotations

from enum import StrEnum

_ALL_SENTINEL = "__all__"


class AutonomyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


AUTONOMY_RULES: dict[AutonomyLevel, set[str]] = {
    AutonomyLevel.LOW: {"read_only"},
    AutonomyLevel.MEDIUM: {
        "read_only",
        "retry",
        "budget_reallocation",
        "concurrency",
        "task_assumption",
    },
    AutonomyLevel.HIGH: {
        "read_only",
        "retry",
        "budget_reallocation",
        "concurrency",
        "task_assumption",
        "scope_change",
        "architecture_decision",
        "understanding_assumption",
    },
    AutonomyLevel.MAX: {_ALL_SENTINEL},
}


def is_autonomous(level: AutonomyLevel, action_type: str) -> bool:
    allowed = AUTONOMY_RULES[level]
    if _ALL_SENTINEL in allowed:
        return True
    return action_type in allowed


def requires_approval(level: AutonomyLevel, action_type: str) -> bool:
    return not is_autonomous(level, action_type)
