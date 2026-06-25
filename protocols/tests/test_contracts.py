from __future__ import annotations

from collections.abc import Awaitable, Callable

from orxtra.protocols import FlushScheduler


class _StubFlushScheduler:
    """Minimal implementation to verify the protocol is runtime-checkable."""

    def schedule_flush(
        self,
        deadline: float,
        callback: Callable[[], Awaitable[None]],
    ) -> object:
        return id(callback)

    def cancel_flush(self, handle: object) -> None:
        pass


class TestFlushSchedulerProtocol:
    def test_runtime_checkable(self) -> None:
        scheduler = _StubFlushScheduler()
        assert isinstance(scheduler, FlushScheduler)

    def test_non_conforming_rejected(self) -> None:
        assert not isinstance(object(), FlushScheduler)
