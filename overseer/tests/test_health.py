from __future__ import annotations

from orxtra.overseer._health import HealthMonitor


def test_fresh_monitor_not_degraded() -> None:
    monitor = HealthMonitor()
    assert not monitor.is_degraded("TaskFailed")


def test_all_successes_stays_healthy() -> None:
    monitor = HealthMonitor(threshold=0.3, window_size=10)
    for _ in range(10):
        monitor.record_event("TaskFailed", success=True)
    assert not monitor.is_degraded("TaskFailed")


def test_failures_above_threshold_degrades() -> None:
    monitor = HealthMonitor(threshold=0.3, window_size=10)
    for _ in range(4):
        monitor.record_event("TaskFailed", success=False)
    for _ in range(6):
        monitor.record_event("TaskFailed", success=True)
    assert monitor.is_degraded("TaskFailed")


def test_recovery_after_failures_drop() -> None:
    monitor = HealthMonitor(threshold=0.3, window_size=5)
    for _ in range(3):
        monitor.record_event("TaskFailed", success=False)
    for _ in range(2):
        monitor.record_event("TaskFailed", success=True)
    assert monitor.is_degraded("TaskFailed")
    for _ in range(5):
        monitor.record_event("TaskFailed", success=True)
    assert not monitor.is_degraded("TaskFailed")


def test_per_event_type_isolation() -> None:
    monitor = HealthMonitor(threshold=0.3, window_size=5)
    for _ in range(4):
        monitor.record_event("TaskFailed", success=False)
    monitor.record_event("TaskFailed", success=True)
    monitor.record_event("BudgetExhausted", success=True)
    assert monitor.is_degraded("TaskFailed")
    assert not monitor.is_degraded("BudgetExhausted")


def test_repetition_tracking_degrades() -> None:
    monitor = HealthMonitor(threshold=0.3, window_size=10)
    for _ in range(4):
        monitor.record_event("TaskFailed", success=True, is_repetition=True)
    for _ in range(6):
        monitor.record_event("TaskFailed", success=True, is_repetition=False)
    assert monitor.is_degraded("TaskFailed")


def test_window_size_drops_old_events() -> None:
    monitor = HealthMonitor(threshold=0.3, window_size=5)
    for _ in range(3):
        monitor.record_event("TaskFailed", success=False)
    for _ in range(2):
        monitor.record_event("TaskFailed", success=True)
    assert monitor.is_degraded("TaskFailed")
    for _ in range(5):
        monitor.record_event("TaskFailed", success=True)
    assert not monitor.is_degraded("TaskFailed")


def test_zero_events_not_degraded() -> None:
    monitor = HealthMonitor()
    assert not monitor.is_degraded("unknown_type")


def test_get_metrics_correct_counts() -> None:
    monitor = HealthMonitor(window_size=10)
    monitor.record_event("test", success=True)
    monitor.record_event("test", success=False)
    monitor.record_event("test", success=True, is_repetition=True)
    monitor.record_event("test", success=False, is_repetition=True)
    metrics = monitor.get_metrics("test")
    assert metrics.total_events == 4
    assert metrics.postcheck_failures == 2
    assert metrics.repetitions == 2
    assert metrics.tool_errors == 1


def test_threshold_edge_exactly_at() -> None:
    monitor = HealthMonitor(threshold=0.5, window_size=10)
    for _ in range(5):
        monitor.record_event("test", success=False)
    for _ in range(5):
        monitor.record_event("test", success=True)
    assert not monitor.is_degraded("test")


def test_threshold_edge_just_above() -> None:
    monitor = HealthMonitor(threshold=0.5, window_size=10)
    for _ in range(6):
        monitor.record_event("test", success=False)
    for _ in range(4):
        monitor.record_event("test", success=True)
    assert monitor.is_degraded("test")
