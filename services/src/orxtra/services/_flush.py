from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class AsyncioFlushScheduler:
    """Concrete FlushScheduler using asyncio.get_running_loop().call_later().

    Satisfies the ``FlushScheduler`` protocol from ``orxtra.protocols``.
    Demand-driven: schedules a deferred callback after ``deadline`` seconds.
    Returns a handle that can be cancelled.
    """

    def schedule_flush(
        self,
        deadline: float,
        callback: Callable[[], Awaitable[None]],
    ) -> object:
        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            deadline, lambda: asyncio.ensure_future(callback()),
        )
        return handle

    def cancel_flush(self, handle: object) -> None:
        if isinstance(handle, asyncio.TimerHandle):
            handle.cancel()
