from __future__ import annotations

import os
import tempfile
from pathlib import Path


async def atomic_write(path: Path, content: str | bytes) -> None:
    """Write content to path atomically via temp+fsync+rename."""
    if not path.parent.exists():
        msg = f"Parent directory does not exist: {path.parent}"
        raise FileNotFoundError(msg)

    data = content.encode("utf-8") if isinstance(content, str) else content

    fd, tmp_path = tempfile.mkstemp(dir=path.parent)
    closed = False
    try:
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        closed = True
        Path(tmp_path).rename(path)  # noqa: ASYNC240
    except BaseException:
        if not closed:
            os.close(fd)
        tmp = Path(tmp_path)
        if tmp.exists():  # noqa: ASYNC240
            tmp.unlink()  # noqa: ASYNC240
        raise
