"""Tests for the multi-edit tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxtra.protocols._tool import ToolError
from orxtra.tool._write_tools import make_multi_edit_tool
from orxtra.write_safety import StaleWriteTracker, WriteQueue

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def queue() -> WriteQueue:
    return WriteQueue()


@pytest.fixture
def tracker() -> StaleWriteTracker:
    return StaleWriteTracker()


class TestMultiEditTool:
    """Tests for make_multi_edit_tool."""

    @pytest.mark.asyncio
    async def test_single_edit(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """A single edit in a batch applies correctly."""
        target = tmp_path / "file.txt"
        target.write_text("hello world")

        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = await tool.execute({
            "edits": [
                {
                    "file": str(target),
                    "old_string": "world",
                    "new_string": "earth",
                },
            ],
        })
        assert target.read_text() == "hello earth"
        assert "All 1 edit(s) succeeded" in result.text

    @pytest.mark.asyncio
    async def test_multiple_edits_different_files(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Multiple edits to different files all apply."""
        file_a = tmp_path / "a.txt"
        file_a.write_text("alpha")
        file_b = tmp_path / "b.txt"
        file_b.write_text("beta")

        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = await tool.execute({
            "edits": [
                {"file": str(file_a), "old_string": "alpha", "new_string": "ALPHA"},
                {"file": str(file_b), "old_string": "beta", "new_string": "BETA"},
            ],
        })
        assert file_a.read_text() == "ALPHA"
        assert file_b.read_text() == "BETA"
        assert "All 2 edit(s) succeeded" in result.text

    @pytest.mark.asyncio
    async def test_multiple_edits_same_file(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Multiple sequential edits to the same file apply in order."""
        target = tmp_path / "file.txt"
        target.write_text("aaa bbb ccc")

        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = await tool.execute({
            "edits": [
                {"file": str(target), "old_string": "aaa", "new_string": "AAA"},
                {"file": str(target), "old_string": "bbb", "new_string": "BBB"},
            ],
        })
        assert target.read_text() == "AAA BBB ccc"
        assert "All 2 edit(s) succeeded" in result.text

    @pytest.mark.asyncio
    async def test_path_escape_rejected(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Edits targeting paths outside read_root are rejected upfront."""
        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="escapes root boundary"):
            await tool.execute({
                "edits": [
                    {
                        "file": "/etc/passwd",
                        "old_string": "x",
                        "new_string": "y",
                    },
                ],
            })

    @pytest.mark.asyncio
    async def test_write_scope_violation_rejected(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Edits outside write scope are rejected upfront."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "forbidden.txt"
        target.write_text("data")

        tool = make_multi_edit_tool(tmp_path, [allowed], queue, tracker, "s1")
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({
                "edits": [
                    {
                        "file": str(target),
                        "old_string": "data",
                        "new_string": "new",
                    },
                ],
            })

    @pytest.mark.asyncio
    async def test_empty_old_string_rejected(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """An edit with empty old_string is rejected upfront."""
        target = tmp_path / "file.txt"
        target.write_text("hello")

        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="old_string must not be empty"):
            await tool.execute({
                "edits": [
                    {"file": str(target), "old_string": "", "new_string": "x"},
                ],
            })

    @pytest.mark.asyncio
    async def test_partial_failure_reports_both(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """When one edit succeeds and another fails, both are reported."""
        good_file = tmp_path / "good.txt"
        good_file.write_text("hello world")
        bad_file = tmp_path / "bad.txt"
        bad_file.write_text("no match here")

        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = await tool.execute({
            "edits": [
                {
                    "file": str(good_file),
                    "old_string": "world",
                    "new_string": "earth",
                },
                {
                    "file": str(bad_file),
                    "old_string": "MISSING",
                    "new_string": "x",
                },
            ],
        })
        # First edit succeeded.
        assert good_file.read_text() == "hello earth"
        # Second edit failed.
        assert bad_file.read_text() == "no match here"
        assert "1 edit(s) failed" in result.text
        assert "1 succeeded" in result.text

    @pytest.mark.asyncio
    async def test_nonexistent_file_failure(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """An edit to a nonexistent file is reported as a failure."""
        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = await tool.execute({
            "edits": [
                {
                    "file": str(tmp_path / "missing.txt"),
                    "old_string": "x",
                    "new_string": "y",
                },
            ],
        })
        assert "FAILED" in result.text
        assert "1 edit(s) failed" in result.text

    @pytest.mark.asyncio
    async def test_empty_edits_array_rejected(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """An empty edits array is rejected by schema validation."""
        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await tool.execute({"edits": []})

    @pytest.mark.asyncio
    async def test_result_data_contains_confirmations(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """The data field contains Confirmation objects for successful edits."""
        target = tmp_path / "file.txt"
        target.write_text("foo bar")

        tool = make_multi_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = await tool.execute({
            "edits": [
                {"file": str(target), "old_string": "foo", "new_string": "FOO"},
            ],
        })
        assert len(result.data) == 1
        assert "edited" in result.data[0].message
