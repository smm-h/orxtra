from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from orxtra.protocols import FlushScheduler, NotificationPort


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


# -- NotificationPort --


class _StubNotificationPort:
    """Minimal implementation to verify the protocol is runtime-checkable."""

    async def create_delivery(
        self,
        target_principal_id: UUID,
        source_ref: str,
        payload: dict[str, Any],
    ) -> UUID:
        return uuid4()

    async def list_for_principal(
        self,
        principal_id: UUID,
        *,
        unacknowledged_only: bool = True,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> list[Any]:
        return []

    async def acknowledge(self, delivery_id: UUID) -> None:
        pass


class TestNotificationPortProtocol:
    def test_runtime_checkable(self) -> None:
        port = _StubNotificationPort()
        assert isinstance(port, NotificationPort)

    def test_non_conforming_rejected(self) -> None:
        assert not isinstance(object(), NotificationPort)

    def test_importable_from_orxtra_protocols(self) -> None:
        """NotificationPort is importable from the top-level package."""
        from orxtra.protocols import NotificationPort as NotifPort

        assert NotifPort is NotificationPort
