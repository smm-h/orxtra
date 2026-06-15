from __future__ import annotations

from orxt.overseer._autonomy import (
    AUTONOMY_RULES,
    AutonomyLevel,
    is_autonomous,
    requires_approval,
)


def test_low_read_only_autonomous() -> None:
    assert is_autonomous(AutonomyLevel.LOW, "read_only")


def test_low_retry_not_autonomous() -> None:
    assert not is_autonomous(AutonomyLevel.LOW, "retry")


def test_medium_retry_autonomous() -> None:
    assert is_autonomous(AutonomyLevel.MEDIUM, "retry")


def test_medium_scope_change_not_autonomous() -> None:
    assert not is_autonomous(AutonomyLevel.MEDIUM, "scope_change")


def test_high_scope_change_autonomous() -> None:
    assert is_autonomous(AutonomyLevel.HIGH, "scope_change")


def test_high_deploy_not_autonomous() -> None:
    assert not is_autonomous(AutonomyLevel.HIGH, "deploy")


def test_max_everything_autonomous() -> None:
    assert is_autonomous(AutonomyLevel.MAX, "deploy")
    assert is_autonomous(AutonomyLevel.MAX, "delete_data")
    assert is_autonomous(AutonomyLevel.MAX, "anything_at_all")


def test_requires_approval_inverse() -> None:
    assert requires_approval(AutonomyLevel.LOW, "retry")
    assert not requires_approval(AutonomyLevel.MEDIUM, "retry")


def test_unknown_action_requires_approval() -> None:
    assert requires_approval(AutonomyLevel.LOW, "unknown_action")
    assert requires_approval(AutonomyLevel.MEDIUM, "unknown_action")
    assert requires_approval(AutonomyLevel.HIGH, "unknown_action")


def test_all_autonomy_levels_valid() -> None:
    assert set(AutonomyLevel) == {
        AutonomyLevel.LOW,
        AutonomyLevel.MEDIUM,
        AutonomyLevel.HIGH,
        AutonomyLevel.MAX,
    }
    for level in AutonomyLevel:
        assert level in AUTONOMY_RULES
