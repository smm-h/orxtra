from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.trace import StorageBackend, TraceWriter
    from orxtra.trace._protocols import KnowledgeHashStorage


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _supports_knowledge_hashes(
    writer: TraceWriter | StorageBackend,
) -> KnowledgeHashStorage | None:
    """Check if the writer supports knowledge hash persistence."""
    from orxtra.trace._protocols import KnowledgeHashStorage  # noqa: PLC0415

    if isinstance(writer, KnowledgeHashStorage):
        return writer
    return None


async def load_knowledge_files(
    knowledge_dir: Path,
    trace_writer: TraceWriter | StorageBackend,
    run_id: UUID,
) -> None:
    if not knowledge_dir.is_dir():  # noqa: ASYNC240
        return

    hash_store = _supports_knowledge_hashes(trace_writer)
    loaded_hashes: dict[str, str] = {}
    if hash_store is not None:
        loaded_hashes = await hash_store.read_knowledge_hashes(run_id)

    for path in sorted(knowledge_dir.iterdir()):  # noqa: ASYNC240
        if path.suffix == ".md":
            await _load_markdown(path, trace_writer, run_id, loaded_hashes, hash_store)
        elif path.suffix == ".toml":
            await _load_toml(path, trace_writer, run_id, loaded_hashes, hash_store)


async def _load_markdown(
    path: Path,
    trace_writer: TraceWriter | StorageBackend,
    run_id: UUID,
    loaded_hashes: dict[str, str],
    hash_store: KnowledgeHashStorage | None,
) -> None:
    file_hash = _file_hash(path)
    cache_key = str(path)
    if loaded_hashes.get(cache_key) == file_hash:
        return
    text = path.read_text(encoding="utf-8")  # noqa: ASYNC240
    await trace_writer.write_lesson(
        run_id=run_id,
        text=text,
        relevance_tags=[path.stem],
        permanent=True,
        source_files=[str(path)],
    )
    loaded_hashes[cache_key] = file_hash
    if hash_store is not None:
        await hash_store.write_knowledge_hash(run_id, cache_key, file_hash)


async def _load_toml(
    path: Path,
    trace_writer: TraceWriter | StorageBackend,
    run_id: UUID,
    loaded_hashes: dict[str, str],
    hash_store: KnowledgeHashStorage | None,
) -> None:
    file_hash = _file_hash(path)
    cache_key = str(path)
    if loaded_hashes.get(cache_key) == file_hash:
        return
    raw = path.read_text(encoding="utf-8")  # noqa: ASYNC240
    data: dict[str, Any] = tomllib.loads(raw)
    constraints: list[dict[str, Any]] = data.get("constraints", [])
    for constraint in constraints:
        text: str = constraint["text"]
        tier: str = constraint.get("tier", "advisory")
        kind: str = constraint.get("kind", "knowledge_file")
        await trace_writer.write_constraint(
            run_id=run_id,
            text=text,
            tier=tier,
            kind=kind,
        )
    loaded_hashes[cache_key] = file_hash
    if hash_store is not None:
        await hash_store.write_knowledge_hash(run_id, cache_key, file_hash)
