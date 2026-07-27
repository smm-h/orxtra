from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orxtra.overseer._gen_knowledge import (
    validate_bytes as _validate_knowledge_document,
)

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.trace import KnowledgeHashStorage, StorageBackend, TraceWriter


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _supports_knowledge_hashes(
    writer: TraceWriter | StorageBackend,
) -> KnowledgeHashStorage | None:
    """Check if the writer supports knowledge hash persistence."""
    from orxtra.trace import KnowledgeHashStorage

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
    # hash_store=None is valid: files load every run but skip-unchanged
    # is unavailable. The write_lesson/write_constraint calls still work
    # on any writer -- only hash persistence requires KnowledgeHashStorage.

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
    # strictspec document gate: enforces integer format_version, TOML parse
    # validity, and per-constraint required text/tier/kind. Subsumes the
    # hand-rolled per-constraint tier/kind presence checks. tier/kind VALUES
    # stay unvalidated (passed through to write_constraint) as before.
    _root, diags = _validate_knowledge_document(raw.encode("utf-8"), "toml")
    if diags:
        detail = "\n".join(f"  {d.code} at {d.path}: {d.message}" for d in diags)
        msg = f"Invalid knowledge document ({path}):\n{detail}"
        raise ValueError(msg)
    data: dict[str, Any] = tomllib.loads(raw)
    constraints: list[dict[str, Any]] = data.get("constraints", [])
    for constraint in constraints:
        text: str = constraint["text"]
        tier: str = constraint["tier"]
        kind: str = constraint["kind"]
        await trace_writer.write_constraint(
            run_id=run_id,
            text=text,
            tier=tier,
            kind=kind,
        )
    loaded_hashes[cache_key] = file_hash
    if hash_store is not None:
        await hash_store.write_knowledge_hash(run_id, cache_key, file_hash)
