from __future__ import annotations

TASK_TRANSITIONS: dict[str, set[str]] = {
    "created": {"prechecking"},
    "prechecking": {"active", "precheck_failed"},
    "active": {"postchecking"},
    "postchecking": {"completed", "postcheck_failed"},
    "postcheck_failed": {"active", "escalated"},
}

TASK_TERMINAL_STATES: set[str] = {
    "completed", "cancelled", "escalated", "precheck_failed",
}

RUN_TRANSITIONS: dict[str, set[str]] = {
    "created": {"running"},
    "running": {"paused", "completed", "failed", "aborted"},
    "paused": {"running", "aborted"},
}

RUN_TERMINAL_STATES: set[str] = {"completed", "failed", "aborted"}


class InvalidTransitionError(Exception):
    pass


def validate_task_transition(old: str, new: str) -> None:
    # Any state -> cancelled is always valid
    if new == "cancelled":
        return
    if old in TASK_TERMINAL_STATES:
        msg = f"cannot transition task from terminal state {old!r} to {new!r}"
        raise InvalidTransitionError(msg)
    allowed = TASK_TRANSITIONS.get(old)
    if allowed is None:
        msg = f"unknown task state {old!r}, cannot transition to {new!r}"
        raise InvalidTransitionError(msg)
    if new not in allowed:
        targets = sorted(allowed)
        msg = f"invalid task transition {old!r} -> {new!r}, allowed: {targets}"
        raise InvalidTransitionError(msg)


def validate_run_transition(old: str, new: str) -> None:
    if old in RUN_TERMINAL_STATES:
        msg = f"cannot transition run from terminal state {old!r} to {new!r}"
        raise InvalidTransitionError(msg)
    allowed = RUN_TRANSITIONS.get(old)
    if allowed is None:
        msg = f"unknown run state {old!r}, cannot transition to {new!r}"
        raise InvalidTransitionError(msg)
    if new not in allowed:
        targets = sorted(allowed)
        msg = f"invalid run transition {old!r} -> {new!r}, allowed: {targets}"
        raise InvalidTransitionError(msg)
