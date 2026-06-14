from __future__ import annotations

import asyncio
import errno
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_TRANSIENT_ERRNOS: frozenset[int] = frozenset({
    errno.EIO,
    errno.EBUSY,
    errno.EAGAIN,
    errno.ENOSPC,
    errno.ENOLCK,
})


def is_transient_error(error: OSError) -> bool:
    """Return True if the error is transient and worth retrying."""
    return error.errno in _TRANSIENT_ERRNOS


async def with_transient_retry[T](
    fn: Callable[..., Awaitable[T]],
    *args: Any,  # noqa: ANN401
    max_retries: int = 3,
    **kwargs: Any,  # noqa: ANN401
) -> T:
    """Call fn, retrying on transient OS errors with exponential backoff."""
    delay = 0.1
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except OSError as exc:
            if not is_transient_error(exc) or attempt == max_retries:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    # Unreachable, but makes mypy happy
    msg = "unreachable"
    raise AssertionError(msg)
