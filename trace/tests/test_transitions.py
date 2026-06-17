from __future__ import annotations

import pytest
from orxt.trace import (
    InvalidTransitionError,
    validate_run_transition,
    validate_task_transition,
)
from orxt.trace._transitions import (
    RUN_TRANSITIONS,
    TASK_TERMINAL_STATES,
    TASK_TRANSITIONS,
)


class TestTaskTransitions:
    def test_all_valid_transitions(self) -> None:
        for old, targets in TASK_TRANSITIONS.items():
            for new in targets:
                validate_task_transition(old, new)

    def test_cancelled_always_valid(self) -> None:
        all_states = set(TASK_TRANSITIONS.keys()) | TASK_TERMINAL_STATES
        for state in all_states:
            validate_task_transition(state, "cancelled")

    def test_invalid_created_to_active(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition("created", "active")

    def test_valid_active_to_completed(self) -> None:
        validate_task_transition("active", "completed")

    def test_valid_active_to_suspended(self) -> None:
        validate_task_transition("active", "suspended")

    def test_valid_suspended_to_active(self) -> None:
        validate_task_transition("suspended", "active")

    def test_suspended_not_terminal(self) -> None:
        assert "suspended" not in TASK_TERMINAL_STATES

    def test_suspended_to_cancelled_valid(self) -> None:
        validate_task_transition("suspended", "cancelled")

    def test_terminal_completed_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition("completed", "active")

    def test_terminal_cancelled_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition("cancelled", "active")

    def test_terminal_escalated_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition("escalated", "active")

    def test_terminal_precheck_failed_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition("precheck_failed", "active")

    def test_unknown_state_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_task_transition("nonexistent", "active")


class TestRunTransitions:
    def test_all_valid_transitions(self) -> None:
        for old, targets in RUN_TRANSITIONS.items():
            for new in targets:
                validate_run_transition(old, new)

    def test_invalid_created_to_completed(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_run_transition("created", "completed")

    def test_invalid_running_to_created(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_run_transition("running", "created")

    def test_terminal_completed_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_run_transition("completed", "running")

    def test_terminal_failed_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_run_transition("failed", "running")

    def test_terminal_aborted_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_run_transition("aborted", "running")

    def test_unknown_state_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_run_transition("nonexistent", "running")
