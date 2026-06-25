from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from orxtra.protocols import (
    Confirmation,
    ConsultResponse,
    DiffResult,
    DirEntry,
    DirListing,
    ExecResult,
    FileContent,
    FileStat,
    GitOutput,
    GrepMatch,
    GrepResult,
    HttpResponse,
    Renderer,
    TaskLifecycleResult,
    ToolOutput,
)


# -- ToolOutput tests -------------------------------------------------------


class TestToolOutputFrozen:
    def test_immutable(self) -> None:
        out = ToolOutput(data="hello", text="hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            out.data = "changed"  # type: ignore[misc]

    def test_immutable_text(self) -> None:
        out = ToolOutput(data=42, text="forty-two")
        with pytest.raises(dataclasses.FrozenInstanceError):
            out.text = "changed"  # type: ignore[misc]


class TestToolOutputGeneric:
    def test_string_data(self) -> None:
        out: ToolOutput[str] = ToolOutput(data="hello", text="hello")
        assert out.data == "hello"
        assert out.text == "hello"

    def test_int_data(self) -> None:
        out: ToolOutput[int] = ToolOutput(data=42, text="42")
        assert out.data == 42

    def test_dict_data(self) -> None:
        d: dict[str, int] = {"a": 1}
        out: ToolOutput[dict[str, int]] = ToolOutput(data=d, text="{a: 1}")
        assert out.data == {"a": 1}

    def test_complex_generic(self) -> None:
        fc = FileContent(
            content="line1", is_preview=False, total_lines=1, total_bytes=5,
        )
        out: ToolOutput[FileContent] = ToolOutput(data=fc, text="line1")
        assert out.data.content == "line1"


# -- Renderer protocol tests ------------------------------------------------


class TestRendererProtocol:
    def test_satisfies_protocol(self) -> None:
        class MyRenderer:
            def render(self, data: str) -> str:
                return data.upper()

        r = MyRenderer()
        assert isinstance(r, Renderer)

    def test_non_renderer_fails(self) -> None:
        class NotARenderer:
            pass

        assert not isinstance(NotARenderer(), Renderer)


# -- FileContent tests ------------------------------------------------------


class TestFileContent:
    def test_construction(self) -> None:
        fc = FileContent(
            content="hello\nworld",
            is_preview=True,
            total_lines=100,
            total_bytes=500,
        )
        assert fc.content == "hello\nworld"
        assert fc.is_preview is True
        assert fc.total_lines == 100
        assert fc.total_bytes == 500

    def test_frozen(self) -> None:
        fc = FileContent(content="x", is_preview=False, total_lines=1, total_bytes=1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            fc.content = "y"  # type: ignore[misc]


# -- DirEntry / DirListing tests -------------------------------------------


class TestDirEntry:
    def test_construction(self) -> None:
        de = DirEntry(type="file", size=1024, path="/a/b.txt")
        assert de.type == "file"
        assert de.size == 1024
        assert de.path == "/a/b.txt"

    def test_size_none(self) -> None:
        de = DirEntry(type="dir", size=None, path="/a/b")
        assert de.size is None

    def test_frozen(self) -> None:
        de = DirEntry(type="file", size=10, path="/x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            de.path = "/y"  # type: ignore[misc]


class TestDirListing:
    def test_construction(self) -> None:
        entries = [DirEntry(type="file", size=10, path="/a")]
        dl = DirListing(entries=entries, truncated=False)
        assert len(dl.entries) == 1
        assert dl.truncated is False

    def test_frozen(self) -> None:
        dl = DirListing(entries=[], truncated=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            dl.truncated = False  # type: ignore[misc]


# -- GrepMatch / GrepResult tests ------------------------------------------


class TestGrepMatch:
    def test_construction(self) -> None:
        gm = GrepMatch(file="/a.py", line_number=42, line="import os")
        assert gm.file == "/a.py"
        assert gm.line_number == 42
        assert gm.line == "import os"


class TestGrepResult:
    def test_construction(self) -> None:
        matches = [GrepMatch(file="/a.py", line_number=1, line="x")]
        gr = GrepResult(matches=matches, mode="regex", count=1)
        assert gr.mode == "regex"
        assert gr.count == 1
        assert len(gr.matches) == 1

    def test_count_none(self) -> None:
        gr = GrepResult(matches=[], mode="literal", count=None)
        assert gr.count is None


# -- FileStat tests ---------------------------------------------------------


class TestFileStat:
    def test_construction(self) -> None:
        fs = FileStat(
            path="/a.py",
            exists=True,
            byte_size=1024,
            line_count=50,
            language="python",
            mtime="2024-01-01T00:00:00",
            binary=False,
        )
        assert fs.path == "/a.py"
        assert fs.exists is True
        assert fs.byte_size == 1024
        assert fs.line_count == 50
        assert fs.language == "python"
        assert fs.binary is False

    def test_nonexistent(self) -> None:
        fs = FileStat(
            path="/missing",
            exists=False,
            byte_size=None,
            line_count=None,
            language=None,
            mtime=None,
            binary=False,
        )
        assert fs.exists is False
        assert fs.byte_size is None


# -- DiffResult tests -------------------------------------------------------


class TestDiffResult:
    def test_construction(self) -> None:
        dr = DiffResult(diff="- old\n+ new", identical=False)
        assert dr.diff == "- old\n+ new"
        assert dr.identical is False

    def test_identical(self) -> None:
        dr = DiffResult(diff="", identical=True)
        assert dr.identical is True


# -- GitOutput tests --------------------------------------------------------


class TestGitOutput:
    def test_construction(self) -> None:
        go = GitOutput(output="abc123", subcommand="rev-parse", exit_code=0)
        assert go.output == "abc123"
        assert go.subcommand == "rev-parse"
        assert go.exit_code == 0


# -- ExecResult tests -------------------------------------------------------


class TestExecResult:
    def test_construction(self) -> None:
        er = ExecResult(
            stdout="ok", stderr="", exit_code=0, timed_out=False, duration_ms=150,
        )
        assert er.stdout == "ok"
        assert er.stderr == ""
        assert er.exit_code == 0
        assert er.timed_out is False
        assert er.duration_ms == 150

    def test_timed_out(self) -> None:
        er = ExecResult(
            stdout="", stderr="killed", exit_code=-1, timed_out=True, duration_ms=30000,
        )
        assert er.timed_out is True


# -- HttpResponse tests -----------------------------------------------------


class TestHttpResponse:
    def test_construction(self) -> None:
        hr = HttpResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            body="OK",
            elapsed_ms=42,
        )
        assert hr.status_code == 200
        assert hr.headers == {"content-type": "text/plain"}
        assert hr.body == "OK"
        assert hr.elapsed_ms == 42


# -- Confirmation tests -----------------------------------------------------


class TestConfirmation:
    def test_construction(self) -> None:
        c = Confirmation(message="File written")
        assert c.message == "File written"

    def test_frozen(self) -> None:
        c = Confirmation(message="ok")
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.message = "changed"  # type: ignore[misc]


# -- ConsultResponse tests --------------------------------------------------


class TestConsultResponse:
    def test_construction(self) -> None:
        cr = ConsultResponse(text="answer", model="gpt-4")
        assert cr.text == "answer"
        assert cr.model == "gpt-4"

    def test_model_none(self) -> None:
        cr = ConsultResponse(text="answer", model=None)
        assert cr.model is None


# -- TaskLifecycleResult tests ----------------------------------------------


class TestTaskLifecycleResult:
    def test_construction(self) -> None:
        tlr = TaskLifecycleResult(
            message="Task started",
            task_id="abc-123",
            details={"status": "running"},
        )
        assert tlr.message == "Task started"
        assert tlr.task_id == "abc-123"
        assert tlr.details == {"status": "running"}

    def test_optional_fields_none(self) -> None:
        tlr = TaskLifecycleResult(message="ok", task_id=None, details=None)
        assert tlr.task_id is None
        assert tlr.details is None

    def test_frozen(self) -> None:
        tlr = TaskLifecycleResult(message="ok", task_id=None, details=None)
        with pytest.raises(dataclasses.FrozenInstanceError):
            tlr.message = "changed"  # type: ignore[misc]
