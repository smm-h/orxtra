from __future__ import annotations

import asyncio
import threading
from typing import Any, TypeVar

T = TypeVar("T")


def run_sync(coro: Any) -> T:  # noqa: ANN401
    """Run an async coroutine from synchronous code.

    Three modes:
    - No running event loop: uses asyncio.run()
    - Running loop on a different thread: uses run_coroutine_threadsafe()
    - Running loop on THIS thread: raises RuntimeError
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if loop._thread_id == threading.get_ident():  # noqa: SLF001
        msg = (
            "run_sync() called from the event loop thread. "
            "Use the async function directly with 'await'."
        )
        raise RuntimeError(msg)

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()
