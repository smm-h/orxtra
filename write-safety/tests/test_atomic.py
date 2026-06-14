from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from orxt.write_safety import atomic_write

if TYPE_CHECKING:
    from pathlib import Path


async def test_write_new_file(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    await atomic_write(target, "hello")
    assert target.read_text() == "hello"


async def test_overwrite_existing(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old")
    await atomic_write(target, "new")
    assert target.read_text() == "new"


async def test_write_binary(tmp_path: Path) -> None:
    target = tmp_path / "binary.bin"
    data = b"\x00\x01\x02\xff"
    await atomic_write(target, data)
    assert target.read_bytes() == data


async def test_parent_not_exists(tmp_path: Path) -> None:
    target = tmp_path / "nonexistent" / "file.txt"
    with pytest.raises(FileNotFoundError, match="Parent directory does not exist"):
        await atomic_write(target, "fail")


async def test_concurrent_writes(tmp_path: Path) -> None:
    target = tmp_path / "concurrent.txt"

    async def write_value(value: str) -> None:
        await atomic_write(target, value)

    tasks = [asyncio.create_task(write_value(str(i))) for i in range(10)]
    await asyncio.gather(*tasks)
    content = target.read_text()
    assert content in {str(i) for i in range(10)}


async def test_failure_cleans_up_temp(tmp_path: Path) -> None:
    target = tmp_path / "fail.txt"
    target.write_text("original")

    with (
        patch("os.fsync", side_effect=OSError("disk error")),
        pytest.raises(OSError, match="disk error"),
    ):
        await atomic_write(target, "should fail")

    assert target.read_text() == "original"
    # No leftover temp files
    remaining = list(tmp_path.iterdir())  # noqa: ASYNC240
    assert len(remaining) == 1
    assert remaining[0].name == "fail.txt"
