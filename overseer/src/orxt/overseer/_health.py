from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class HealthMetrics:
    postcheck_failures: int = 0
    repetitions: int = 0
    tool_errors: int = 0
    total_events: int = 0


@dataclass(frozen=True)
class _Event:
    success: bool
    is_repetition: bool


class HealthMonitor:
    def __init__(self, threshold: float = 0.3, window_size: int = 10) -> None:
        self._threshold = threshold
        self._window_size = window_size
        self._windows: dict[str, deque[_Event]] = {}

    def _get_window(self, event_type: str) -> deque[_Event]:
        if event_type not in self._windows:
            self._windows[event_type] = deque(maxlen=self._window_size)
        return self._windows[event_type]

    def record_event(
        self, event_type: str, success: bool, is_repetition: bool = False,
    ) -> None:
        window = self._get_window(event_type)
        window.append(_Event(success=success, is_repetition=is_repetition))

    def is_degraded(self, event_type: str) -> bool:
        window = self._get_window(event_type)
        if len(window) == 0:
            return False
        metrics = self._compute_metrics(window)
        failure_rate = (
            metrics.postcheck_failures / metrics.total_events
            if metrics.total_events > 0
            else 0.0
        )
        repetition_rate = (
            metrics.repetitions / metrics.total_events
            if metrics.total_events > 0
            else 0.0
        )
        tool_error_rate = (
            metrics.tool_errors / metrics.total_events
            if metrics.total_events > 0
            else 0.0
        )
        return (
            failure_rate > self._threshold
            or repetition_rate > self._threshold
            or tool_error_rate > self._threshold
        )

    def get_metrics(self, event_type: str) -> HealthMetrics:
        window = self._get_window(event_type)
        return self._compute_metrics(window)

    @staticmethod
    def _compute_metrics(window: deque[_Event]) -> HealthMetrics:
        total = len(window)
        failures = sum(1 for e in window if not e.success)
        repetitions = sum(1 for e in window if e.is_repetition)
        tool_errors = sum(
            1 for e in window if not e.success and not e.is_repetition
        )
        return HealthMetrics(
            postcheck_failures=failures,
            repetitions=repetitions,
            tool_errors=tool_errors,
            total_events=total,
        )
