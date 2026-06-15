from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from orxt.trace import TraceWriter

_loaded_hashes: dict[str, str] = {}


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def load_knowledge_files(
    knowledge_dir: Path,
    trace_writer: TraceWriter,
    run_id: UUID,
) -> None:
    if not knowledge_dir.is_dir():  # noqa: ASYNC240
        return

    for path in sorted(knowledge_dir.iterdir()):  # noqa: ASYNC240
        if path.suffix == ".md":
            await _load_markdown(path, trace_writer, run_id)
        elif path.suffix == ".toml":
            await _load_toml(path, trace_writer, run_id)


async def _load_markdown(
    path: Path, trace_writer: TraceWriter, run_id: UUID,
) -> None:
    file_hash = _file_hash(path)
    cache_key = str(path)
    if _loaded_hashes.get(cache_key) == file_hash:
        return
    text = path.read_text(encoding="utf-8")  # noqa: ASYNC240
    await trace_writer.write_lesson(
        run_id=run_id,
        text=text,
        relevance_tags=[path.stem],
        permanent=True,
        source_file=str(path),
    )
    _loaded_hashes[cache_key] = file_hash


async def _load_toml(
    path: Path, trace_writer: TraceWriter, run_id: UUID,
) -> None:
    file_hash = _file_hash(path)
    cache_key = str(path)
    if _loaded_hashes.get(cache_key) == file_hash:
        return
    raw = path.read_text(encoding="utf-8")  # noqa: ASYNC240
    data: dict[str, Any] = tomllib.loads(raw)
    constraints: list[dict[str, Any]] = data.get("constraints", [])
    for constraint in constraints:
        text: str = constraint["text"]
        tier: str = constraint.get("tier", "advisory")
        await trace_writer.write_constraint(
            run_id=run_id,
            text=text,
            tier=tier,
        )
    _loaded_hashes[cache_key] = file_hash
