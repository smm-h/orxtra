"""Tests for the compose sub-project.

Covers: include resolution, variable substitution, Fragment model,
FragmentProvider protocol, CompositionEngine, FileFragmentProvider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from orxtra.compose import (
    CompositionEngine,
    FileFragmentProvider,
    Fragment,
    FragmentProvider,
    resolve_includes,
    resolve_variables,
)
from pydantic import ValidationError

# --- Include resolution (ported from agent/tests/test_prompt.py) ---


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


# --- Variable substitution (ported from agent/tests/test_prompt.py) ---


class TestResolveVariables:
    def test_single_variable(self) -> None:
        result = resolve_variables("Hello {name}!", {"name": "world"})
        assert result == "Hello world!"

    def test_multiple_variables(self) -> None:
        result = resolve_variables("{a} and {b}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_unresolved_placeholder_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Unresolved placeholder.*missing"):
            resolve_variables("Hello {missing}!", {})

    def test_unused_variable_raises(self) -> None:
        with pytest.raises(ValueError, match=r"Unused variable.*extra"):
            resolve_variables("Hello!", {"extra": "value"})

    def test_no_variables_no_placeholders(self) -> None:
        result = resolve_variables("plain text", {})
        assert result == "plain text"

    def test_does_not_touch_include_syntax(self) -> None:
        result = resolve_variables("{include:file.md}", {})
        assert result == "{include:file.md}"

    def test_variable_value_with_braces(self) -> None:
        result = resolve_variables("{x}", {"x": "{not_a_var}"})
        assert result == "{not_a_var}"

    def test_empty_template(self) -> None:
        result = resolve_variables("", {})
        assert result == ""

    def test_same_variable_twice(self) -> None:
        result = resolve_variables("{x} and {x}", {"x": "V"})
        assert result == "V and V"


# --- Fragment model ---


class TestFragment:
    def test_create_with_defaults(self) -> None:
        f = Fragment(name="test", content="hello", source="unit")
        assert f.name == "test"
        assert f.content == "hello"
        assert f.priority == 0
        assert f.source == "unit"

    def test_create_with_priority(self) -> None:
        f = Fragment(name="x", content="y", priority=10, source="s")
        assert f.priority == 10

    def test_frozen(self) -> None:
        f = Fragment(name="x", content="y", source="s")
        with pytest.raises(ValidationError, match="frozen"):
            f.name = "z"  # type: ignore[misc]

    def test_forbids_extra(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            Fragment(name="x", content="y", source="s", extra="bad")  # type: ignore[call-arg]


# --- FragmentProvider protocol ---


class TestFragmentProvider:
    def test_mock_provider_satisfies_protocol(self) -> None:
        class MockProvider:
            def __init__(self, frags: list[Fragment]) -> None:
                self._frags = frags

            def fragments(self, context: dict[str, Any]) -> list[Fragment]:
                return self._frags

        provider = MockProvider([Fragment(name="a", content="A", source="mock")])
        assert isinstance(provider, FragmentProvider)
        result = provider.fragments({})
        assert len(result) == 1
        assert result[0].name == "a"

    def test_context_is_passed(self) -> None:
        class ContextAware:
            def fragments(self, context: dict[str, Any]) -> list[Fragment]:
                task_id = context.get("task_id", "unknown")
                return [
                    Fragment(
                        name="ctx",
                        content=f"task={task_id}",
                        source="context-aware",
                    )
                ]

        provider = ContextAware()
        result = provider.fragments({"task_id": "T-42"})
        assert result[0].content == "task=T-42"


# --- CompositionEngine ---


class TestCompositionEngine:
    def _make_provider(
        self, frags: list[Fragment]
    ) -> FragmentProvider:
        class StaticProvider:
            def __init__(self, f: list[Fragment]) -> None:
                self._f = f

            def fragments(self, context: dict[str, Any]) -> list[Fragment]:
                return self._f

        return StaticProvider(frags)

    def test_single_provider(self) -> None:
        p = self._make_provider(
            [Fragment(name="a", content="hello", source="test")]
        )
        engine = CompositionEngine([p])
        result = engine.compose({})
        assert result == "hello"

    def test_multiple_providers(self) -> None:
        p1 = self._make_provider(
            [Fragment(name="a", content="AAA", source="p1")]
        )
        p2 = self._make_provider(
            [Fragment(name="b", content="BBB", source="p2")]
        )
        engine = CompositionEngine([p1, p2])
        result = engine.compose({})
        assert "AAA" in result
        assert "BBB" in result

    def test_priority_ordering(self) -> None:
        p = self._make_provider([
            Fragment(name="low", content="LOW", priority=10, source="t"),
            Fragment(name="high", content="HIGH", priority=1, source="t"),
        ])
        engine = CompositionEngine([p])
        result = engine.compose({})
        # HIGH (priority 1) should come before LOW (priority 10)
        assert result.index("HIGH") < result.index("LOW")

    def test_deterministic_tiebreak(self) -> None:
        p = self._make_provider([
            Fragment(name="beta", content="BETA", priority=0, source="t"),
            Fragment(name="alpha", content="ALPHA", priority=0, source="t"),
        ])
        engine = CompositionEngine([p])
        result = engine.compose({})
        # Same priority -> alphabetical by name: alpha before beta
        assert result.index("ALPHA") < result.index("BETA")

    def test_empty_providers(self) -> None:
        p = self._make_provider([])
        engine = CompositionEngine([p])
        result = engine.compose({})
        assert result == ""

    def test_no_providers(self) -> None:
        engine = CompositionEngine([])
        result = engine.compose({})
        assert result == ""

    def test_variable_substitution(self) -> None:
        p = self._make_provider(
            [Fragment(name="a", content="Hello {who}!", source="t")]
        )
        engine = CompositionEngine([p])
        result = engine.compose({}, variables={"who": "world"})
        assert result == "Hello world!"

    def test_variable_substitution_unresolved_raises(self) -> None:
        p = self._make_provider(
            [Fragment(name="a", content="Hello {missing}!", source="t")]
        )
        engine = CompositionEngine([p])
        with pytest.raises(ValueError, match="Unresolved placeholder"):
            engine.compose({}, variables={})

    def test_variable_substitution_unused_raises(self) -> None:
        p = self._make_provider(
            [Fragment(name="a", content="plain", source="t")]
        )
        engine = CompositionEngine([p])
        with pytest.raises(ValueError, match="Unused variable"):
            engine.compose({}, variables={"extra": "value"})

    def test_no_variables_no_substitution(self) -> None:
        p = self._make_provider(
            [Fragment(name="a", content="{keep_this}", source="t")]
        )
        engine = CompositionEngine([p])
        # variables=None means no substitution at all
        result = engine.compose({})
        assert result == "{keep_this}"

    def test_section_separator(self) -> None:
        p = self._make_provider([
            Fragment(name="a", content="FIRST", priority=0, source="t"),
            Fragment(name="b", content="SECOND", priority=1, source="t"),
        ])
        engine = CompositionEngine([p])
        result = engine.compose({})
        assert result == "FIRST\n\n---\n\nSECOND"

    def test_context_passed_to_providers(self) -> None:
        class ContextProvider:
            def fragments(self, context: dict[str, Any]) -> list[Fragment]:
                return [
                    Fragment(
                        name="ctx",
                        content=f"attempt={context.get('attempt', 0)}",
                        source="ctx",
                    )
                ]

        engine = CompositionEngine([ContextProvider()])
        result = engine.compose({"attempt": 3})
        assert result == "attempt=3"


# --- FileFragmentProvider ---


class TestFileFragmentProvider:
    def test_discovers_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.md").write_text("Alpha content")
        (tmp_path / "beta.md").write_text("Beta content")
        (tmp_path / "not_md.txt").write_text("ignored")

        provider = FileFragmentProvider(tmp_path)
        frags = provider.fragments({})
        assert len(frags) == 2
        names = {f.name for f in frags}
        assert names == {"alpha", "beta"}

    def test_resolves_includes(self, tmp_path: Path) -> None:
        (tmp_path / "header.md").write_text("# Title")
        (tmp_path / "main.md").write_text("Before\n{include:header.md}\nAfter")

        provider = FileFragmentProvider(tmp_path)
        frags = provider.fragments({})
        main_frag = next(f for f in frags if f.name == "main")
        assert "# Title" in main_frag.content
        assert "{include:" not in main_frag.content

    def test_returns_fragments(self, tmp_path: Path) -> None:
        (tmp_path / "doc.md").write_text("some text")

        provider = FileFragmentProvider(tmp_path, priority=5)
        frags = provider.fragments({})
        assert len(frags) == 1
        assert frags[0].name == "doc"
        assert frags[0].content == "some text"
        assert frags[0].priority == 5
        assert frags[0].source == f"file:{tmp_path}"

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        provider = FileFragmentProvider(tmp_path / "nonexistent")
        frags = provider.fragments({})
        assert frags == []

    def test_empty_directory(self, tmp_path: Path) -> None:
        provider = FileFragmentProvider(tmp_path)
        frags = provider.fragments({})
        assert frags == []

    def test_satisfies_protocol(self) -> None:
        provider = FileFragmentProvider(Path())
        assert isinstance(provider, FragmentProvider)

    def test_alphabetical_ordering(self, tmp_path: Path) -> None:
        (tmp_path / "zebra.md").write_text("Z")
        (tmp_path / "aardvark.md").write_text("A")

        provider = FileFragmentProvider(tmp_path)
        frags = provider.fragments({})
        assert frags[0].name == "aardvark"
        assert frags[1].name == "zebra"
