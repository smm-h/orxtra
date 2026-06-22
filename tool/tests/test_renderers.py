from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from orxtra.protocols._results import Confirmation, DirEntry, DirListing
from orxtra.tool._renderers import JsonRenderer, TableRenderer, TextRenderer


# -- TextRenderer tests -----------------------------------------------------


class TestTextRendererWithString:
    def test_returns_string_unchanged(self) -> None:
        r = TextRenderer()
        assert r.render("hello world") == "hello world"


class TestTextRendererWithConfirmation:
    def test_renders_confirmation_via_str(self) -> None:
        r = TextRenderer()
        c = Confirmation(message="File written")
        result = r.render(c)
        assert "File written" in result


class TestTextRendererWithInt:
    def test_renders_int(self) -> None:
        r = TextRenderer()
        assert r.render(42) == "42"


class TestTextRendererWithNone:
    def test_renders_none(self) -> None:
        r = TextRenderer()
        assert r.render(None) == "None"


# -- JsonRenderer tests -----------------------------------------------------


class TestJsonRendererWithDict:
    def test_renders_dict(self) -> None:
        r = JsonRenderer()
        result = r.render({"key": "value", "num": 42})
        parsed = json.loads(result)
        assert parsed == {"key": "value", "num": 42}


class TestJsonRendererWithDataclass:
    def test_renders_dataclass(self) -> None:
        r = JsonRenderer()
        c = Confirmation(message="ok")
        result = r.render(c)
        parsed = json.loads(result)
        assert parsed == {"message": "ok"}


class TestJsonRendererWithNestedDataclass:
    def test_renders_nested(self) -> None:
        r = JsonRenderer()
        dl = DirListing(
            entries=[
                DirEntry(type="file", size=100, path="/a.txt"),
                DirEntry(type="dir", size=None, path="/b"),
            ],
            truncated=False,
        )
        result = r.render(dl)
        parsed = json.loads(result)
        assert parsed["truncated"] is False
        assert len(parsed["entries"]) == 2
        assert parsed["entries"][0]["type"] == "file"
        assert parsed["entries"][1]["size"] is None


class TestJsonRendererWithList:
    def test_renders_list(self) -> None:
        r = JsonRenderer()
        result = r.render([1, 2, 3])
        parsed = json.loads(result)
        assert parsed == [1, 2, 3]


class TestJsonRendererIndent:
    def test_custom_indent(self) -> None:
        r = JsonRenderer(indent=4)
        result = r.render({"a": 1})
        assert "    " in result  # 4-space indent


class TestJsonRendererDefaultIndent:
    def test_default_indent(self) -> None:
        r = JsonRenderer()
        result = r.render({"a": 1})
        # Default is 2-space indent
        assert "  " in result


# -- TableRenderer tests ----------------------------------------------------


class TestTableRendererWithDirListing:
    def test_renders_dir_listing(self) -> None:
        r = TableRenderer()
        dl = DirListing(
            entries=[
                DirEntry(type="file", size=100, path="/a.txt"),
                DirEntry(type="dir", size=None, path="/b"),
            ],
            truncated=False,
        )
        result = r.render(dl)
        lines = result.strip().split("\n")
        assert len(lines) == 3  # header + 2 rows
        assert "type" in lines[0]
        assert "size" in lines[0]
        assert "path" in lines[0]
        assert "/a.txt" in lines[1]
        assert "/b" in lines[2]


class TestTableRendererWithEmptyList:
    def test_renders_empty(self) -> None:
        r = TableRenderer()
        assert r.render([]) == ""


class TestTableRendererWithDicts:
    def test_renders_list_of_dicts(self) -> None:
        r = TableRenderer()
        data = [{"name": "a", "count": 1}, {"name": "b", "count": 2}]
        result = r.render(data)
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert "name" in lines[0]
        assert "count" in lines[0]


class TestTableRendererWithDataclasses:
    def test_renders_list_of_dataclasses(self) -> None:
        @dataclass(frozen=True)
        class Row:
            x: int
            y: str

        r = TableRenderer()
        result = r.render([Row(x=1, y="a"), Row(x=2, y="b")])
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert "x\ty" == lines[0]


class TestTableRendererEmptyDirListing:
    def test_empty_entries(self) -> None:
        r = TableRenderer()
        dl = DirListing(entries=[], truncated=False)
        assert r.render(dl) == ""
