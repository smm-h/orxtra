from __future__ import annotations

import errno
from unittest.mock import AsyncMock, call, patch

import pytest
from orxt.write_safety import is_transient_error, with_transient_retry


def test_transient_eio() -> None:
    assert is_transient_error(OSError(errno.EIO, "I/O error"))


def test_transient_ebusy() -> None:
    assert is_transient_error(OSError(errno.EBUSY, "busy"))


def test_transient_eagain() -> None:
    assert is_transient_error(OSError(errno.EAGAIN, "try again"))


def test_transient_enospc() -> None:
    assert is_transient_error(OSError(errno.ENOSPC, "no space"))


def test_transient_enolck() -> None:
    assert is_transient_error(OSError(errno.ENOLCK, "no lock"))


def test_not_transient_enoent() -> None:
    assert not is_transient_error(OSError(errno.ENOENT, "not found"))


def test_not_transient_eacces() -> None:
    assert not is_transient_error(OSError(errno.EACCES, "permission denied"))


async def test_retry_succeeds_first_try() -> None:
    async def ok() -> str:
        return "done"

    result = await with_transient_retry(ok)
    assert result == "done"


async def test_retry_transient_then_success() -> None:
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError(errno.EIO, "transient")
        return "ok"

    result = await with_transient_retry(flaky)
    assert result == "ok"
    assert calls == 3


async def test_retry_exceeds_max() -> None:
    async def always_fail() -> str:
        raise OSError(errno.EIO, "always fails")

    with pytest.raises(OSError, match="always fails"):
        await with_transient_retry(always_fail, max_retries=2)


async def test_non_transient_oserror_no_retry() -> None:
    calls = 0

    async def perm_error() -> str:
        nonlocal calls
        calls += 1
        raise OSError(errno.ENOENT, "not found")

    with pytest.raises(OSError, match="not found"):
        await with_transient_retry(perm_error)
    assert calls == 1


async def test_non_oserror_no_retry() -> None:
    calls = 0

    async def value_error() -> str:
        nonlocal calls
        calls += 1
        msg = "bad value"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="bad value"):
        await with_transient_retry(value_error)
    assert calls == 1


@patch("orxt.write_safety._replay.asyncio.sleep", new_callable=AsyncMock)
async def test_retry_backoff_timing(mock_sleep: AsyncMock) -> None:
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise OSError(errno.EIO, "transient")
        return "ok"

    result = await with_transient_retry(flaky, max_retries=3)
    assert result == "ok"
    assert mock_sleep.call_count == 3
    assert mock_sleep.call_args_list == [call(0.1), call(0.2), call(0.4)]


async def test_retry_forwards_kwargs() -> None:
    async def fn(a: int, *, key: str) -> str:
        return f"{a}-{key}"

    result = await with_transient_retry(fn, 42, key="hello")
    assert result == "42-hello"
