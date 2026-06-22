"""Tests for LivenessMonitor stuck detection."""

from __future__ import annotations

import asyncio

import pytest
from orxtra.transport._liveness import LivenessMonitor


class TestLivenessMonitor:
    """Unit tests for LivenessMonitor state tracking."""

    async def test_check_returns_none_before_any_event(self) -> None:
        """check() returns None if record_event() was never called."""
        monitor = LivenessMonitor()
        assert monitor.check() is None

    async def test_healthy_shortly_after_event(self) -> None:
        """check() returns 'healthy' right after record_event()."""
        monitor = LivenessMonitor(
            health_threshold=30.0,
            stuck_threshold=120.0,
        )
        monitor.record_event()
        assert monitor.check() == "healthy"

    async def test_warning_after_health_threshold(self) -> None:
        """check() returns 'warning' after health_threshold elapses."""
        monitor = LivenessMonitor(
            health_threshold=0.01,
            stuck_threshold=1.0,
        )
        monitor.record_event()
        # Wait past health threshold
        await asyncio.sleep(0.02)
        assert monitor.check() == "warning"

    async def test_stuck_after_stuck_threshold(self) -> None:
        """check() returns 'stuck' after stuck_threshold elapses."""
        monitor = LivenessMonitor(
            health_threshold=0.005,
            stuck_threshold=0.01,
        )
        monitor.record_event()
        # Wait past stuck threshold
        await asyncio.sleep(0.02)
        assert monitor.check() == "stuck"

    async def test_reset_clears_state(self) -> None:
        """reset() returns check() to None."""
        monitor = LivenessMonitor()
        monitor.record_event()
        assert monitor.check() is not None
        monitor.reset()
        assert monitor.check() is None

    async def test_record_event_refreshes_timer(self) -> None:
        """A new record_event() resets the timer to healthy."""
        monitor = LivenessMonitor(
            health_threshold=0.01,
            stuck_threshold=1.0,
        )
        monitor.record_event()
        await asyncio.sleep(0.02)
        assert monitor.check() == "warning"
        # Refresh the timer
        monitor.record_event()
        assert monitor.check() == "healthy"

    async def test_elapsed_property(self) -> None:
        """elapsed returns seconds since last event."""
        monitor = LivenessMonitor()
        assert monitor.elapsed is None
        monitor.record_event()
        await asyncio.sleep(0.01)
        elapsed = monitor.elapsed
        assert elapsed is not None
        assert elapsed >= 0.01

    async def test_healthy_transitions_to_warning_to_stuck(self) -> None:
        """Full lifecycle: healthy -> warning -> stuck."""
        monitor = LivenessMonitor(
            health_threshold=0.01,
            stuck_threshold=0.03,
        )
        monitor.record_event()
        assert monitor.check() == "healthy"

        await asyncio.sleep(0.02)
        assert monitor.check() == "warning"

        await asyncio.sleep(0.02)
        assert monitor.check() == "stuck"


class TestLivenessMonitorDefaults:
    """Test default threshold values."""

    async def test_default_thresholds(self) -> None:
        """Default thresholds are 30s health, 120s stuck."""
        monitor = LivenessMonitor()
        # Just verify it constructs with defaults and check works
        monitor.record_event()
        assert monitor.check() == "healthy"
