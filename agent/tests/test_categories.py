from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxt.agent import Agent, load_categories, resolve_category

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadCategories:
    def test_valid_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "categories.toml"
        path.write_text(
            '[categories]\nfast = "claude-3-haiku"\nsmart = "claude-3-opus"\n'
        )
        result = load_categories(path)
        assert result == {"fast": "claude-3-haiku", "smart": "claude-3-opus"}

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Categories file not found"):
            load_categories(tmp_path / "missing.toml")

    def test_missing_categories_section_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.toml"
        path.write_text("[other]\nkey = 'value'\n")
        with pytest.raises(ValueError, match=r"Missing.*categories.*section"):
            load_categories(path)


class TestResolveCategory:
    def test_known_category(self) -> None:
        agent = Agent(
            name="a", description="d", prompt="p", category="fast", allow=[]
        )
        result = resolve_category(agent, {"fast": "claude-3-haiku"})
        assert result == "claude-3-haiku"

    def test_unknown_category_raises(self) -> None:
        agent = Agent(
            name="a", description="d", prompt="p", category="unknown", allow=[]
        )
        with pytest.raises(ValueError, match=r"Unknown category.*unknown"):
            resolve_category(agent, {"fast": "claude-3-haiku"})
