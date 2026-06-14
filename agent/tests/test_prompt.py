from __future__ import annotations

from pathlib import Path

import pytest
from orxt.agent import resolve_includes, resolve_prompt


class TestResolveIncludes:
    def test_single_include(self, tmp_path: Path) -> None:
        (tmp_path / "header.md").write_text("# Header")
        template = "Before\n{include:header.md}\nAfter"
        result = resolve_includes(template, tmp_path)
        assert result == "Before\n# Header\nAfter"

    def test_nested_includes(self, tmp_path: Path) -> None:
        (tmp_path / "c.md").write_text("leaf")
        (tmp_path / "b.md").write_text("B:{include:c.md}")
        template = "A:{include:b.md}"
        result = resolve_includes(template, tmp_path)
        assert result == "A:B:leaf"

    def test_circular_include_raises(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("{include:b.md}")
        (tmp_path / "b.md").write_text("{include:a.md}")
        with pytest.raises(ValueError, match="Circular include"):
            resolve_includes("{include:a.md}", tmp_path)

    def test_missing_include_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Include file not found"):
            resolve_includes("{include:missing.md}", tmp_path)

    def test_no_includes(self) -> None:
        result = resolve_includes("plain text", Path())
        assert result == "plain text"

    def test_multiple_includes(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("AAA")
        (tmp_path / "b.md").write_text("BBB")
        template = "{include:a.md} and {include:b.md}"
        result = resolve_includes(template, tmp_path)
        assert result == "AAA and BBB"


class TestResolvePrompt:
    def test_single_variable(self) -> None:
        result = resolve_prompt("Hello {name}!", {"name": "world"})
        assert result == "Hello world!"

    def test_multiple_variables(self) -> None:
        result = resolve_prompt("{a} and {b}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_unresolved_placeholder_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Unresolved placeholder.*missing"):
            resolve_prompt("Hello {missing}!", {})

    def test_unused_variable_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Unused variable.*extra"):
            resolve_prompt("Hello!", {"extra": "value"})

    def test_no_variables_no_placeholders(self) -> None:
        result = resolve_prompt("plain text", {})
        assert result == "plain text"

    def test_does_not_touch_include_syntax(self) -> None:
        result = resolve_prompt("{include:file.md}", {})
        assert result == "{include:file.md}"

    def test_variable_value_with_braces(self) -> None:
        result = resolve_prompt("{x}", {"x": "{not_a_var}"})
        assert result == "{not_a_var}"

    def test_empty_template(self) -> None:
        result = resolve_prompt("", {})
        assert result == ""

    def test_same_variable_twice(self) -> None:
        result = resolve_prompt("{x} and {x}", {"x": "V"})
        assert result == "V and V"
