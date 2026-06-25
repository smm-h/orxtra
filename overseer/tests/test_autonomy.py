from __future__ import annotations

from orxtra.overseer._autonomy import AutonomyLevel


def test_low_read_only_autonomous() -> None:
    assert AutonomyLevel.LOW.is_autonomous("read_only")


def test_low_retry_not_autonomous() -> None:
    assert not AutonomyLevel.LOW.is_autonomous("retry")


def test_medium_retry_autonomous() -> None:
    assert AutonomyLevel.MEDIUM.is_autonomous("retry")


def test_medium_scope_change_not_autonomous() -> None:
    assert not AutonomyLevel.MEDIUM.is_autonomous("scope_change")


def test_high_scope_change_autonomous() -> None:
    assert AutonomyLevel.HIGH.is_autonomous("scope_change")


def test_high_deploy_not_autonomous() -> None:
    assert not AutonomyLevel.HIGH.is_autonomous("deploy")


def test_max_everything_autonomous() -> None:
    assert AutonomyLevel.MAX.is_autonomous("deploy")
    assert AutonomyLevel.MAX.is_autonomous("delete_data")
    assert AutonomyLevel.MAX.is_autonomous("anything_at_all")


def test_requires_approval_inverse() -> None:
    assert AutonomyLevel.LOW.requires_approval("retry")
    assert not AutonomyLevel.MEDIUM.requires_approval("retry")


def test_unknown_action_requires_approval() -> None:
    assert AutonomyLevel.LOW.requires_approval("unknown_action")
    assert AutonomyLevel.MEDIUM.requires_approval("unknown_action")
    assert AutonomyLevel.HIGH.requires_approval("unknown_action")


def test_all_autonomy_levels_valid() -> None:
    assert set(AutonomyLevel) == {
        AutonomyLevel.LOW,
        AutonomyLevel.MEDIUM,
        AutonomyLevel.HIGH,
        AutonomyLevel.MAX,
    }
    for level in AutonomyLevel:
        assert level.value in AutonomyLevel._RULES
