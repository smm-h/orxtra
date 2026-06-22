from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import uuid6
from conftest import MockTraceWriter
from orxtra.overseer._knowledge import (
    _loaded_hashes,
    load_knowledge_files,
)

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID


@pytest.fixture
def tw() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def run_id() -> UUID:
    return uuid6.uuid7()


@pytest.fixture(autouse=True)
def _clear_hash_cache() -> None:
    _loaded_hashes.clear()


@pytest.mark.asyncio
async def test_load_md_files(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    (tmp_path / "domain.md").write_text(
        "Domain knowledge here", encoding="utf-8",
    )
    await load_knowledge_files(tmp_path, tw, run_id)
    assert len(tw.calls) == 1
    assert tw.calls[0][0] == "write_lesson"
    assert tw.calls[0][1]["permanent"] is True
    assert tw.calls[0][1]["text"] == "Domain knowledge here"


@pytest.mark.asyncio
async def test_load_toml_with_constraints(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    (tmp_path / "rules.toml").write_text(
        '[[constraints]]\n'
        'text = "All tests must pass"\n'
        'tier = "mechanical"\n',
        encoding="utf-8",
    )
    await load_knowledge_files(tmp_path, tw, run_id)
    assert len(tw.calls) == 1
    assert tw.calls[0][0] == "write_constraint"
    assert tw.calls[0][1]["text"] == "All tests must pass"
    assert tw.calls[0][1]["tier"] == "mechanical"


@pytest.mark.asyncio
async def test_empty_knowledge_dir(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    await load_knowledge_files(tmp_path, tw, run_id)
    assert len(tw.calls) == 0


@pytest.mark.asyncio
async def test_mixed_md_and_toml(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    (tmp_path / "a.md").write_text("lesson a", encoding="utf-8")
    (tmp_path / "b.toml").write_text(
        '[[constraints]]\n'
        'text = "constraint b"\n'
        'tier = "advisory"\n',
        encoding="utf-8",
    )
    await load_knowledge_files(tmp_path, tw, run_id)
    method_names = [c[0] for c in tw.calls]
    assert "write_lesson" in method_names
    assert "write_constraint" in method_names


@pytest.mark.asyncio
async def test_non_md_toml_files_ignored(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    (tmp_path / "readme.txt").write_text(
        "ignored", encoding="utf-8",
    )
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    await load_knowledge_files(tmp_path, tw, run_id)
    assert len(tw.calls) == 0


@pytest.mark.asyncio
async def test_invalid_toml_raises(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    (tmp_path / "bad.toml").write_text(
        "this is not valid toml [[[", encoding="utf-8",
    )
    with pytest.raises(Exception):  # noqa: B017, PT011
        await load_knowledge_files(tmp_path, tw, run_id)


@pytest.mark.asyncio
async def test_md_written_as_permanent(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    (tmp_path / "guide.md").write_text(
        "permanent knowledge", encoding="utf-8",
    )
    await load_knowledge_files(tmp_path, tw, run_id)
    assert tw.calls[0][1]["permanent"] is True
    assert tw.calls[0][1]["source_files"] == [str(
        tmp_path / "guide.md",
    )]


@pytest.mark.asyncio
async def test_constraint_tier_preserved(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    toml_content = (
        '[[constraints]]\ntext = "mech"\ntier = "mechanical"\n\n'
        '[[constraints]]\ntext = "adv"\ntier = "advisory"\n'
    )
    (tmp_path / "rules.toml").write_text(
        toml_content, encoding="utf-8",
    )
    await load_knowledge_files(tmp_path, tw, run_id)
    tiers = [c[1]["tier"] for c in tw.calls]
    assert "mechanical" in tiers
    assert "advisory" in tiers


@pytest.mark.asyncio
async def test_content_hash_skips_unchanged(
    tmp_path: Path, tw: MockTraceWriter, run_id: UUID,
) -> None:
    (tmp_path / "stable.md").write_text(
        "unchanged content", encoding="utf-8",
    )
    await load_knowledge_files(tmp_path, tw, run_id)
    assert len(tw.calls) == 1
    await load_knowledge_files(tmp_path, tw, run_id)
    assert len(tw.calls) == 1


@pytest.mark.asyncio
async def test_nonexistent_dir(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    from pathlib import Path  # noqa: PLC0415

    await load_knowledge_files(
        Path("/nonexistent/dir"), tw, run_id,
    )
    assert len(tw.calls) == 0
