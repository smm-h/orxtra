from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxtra.agent import load_agent, load_agents
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path


def _write_agent(  # noqa: PLR0913
    directory: Path,
    filename: str,
    name: str,
    description: str,
    prompt_file: str,
    category: str,
    allow: list[str],
) -> Path:
    allow_str = ", ".join(f'"{a}"' for a in allow)
    toml_content = (
        f'[agent]\nname = "{name}"\ndescription = "{description}"\n'
        f'prompt = "{prompt_file}"\ncategory = "{category}"\n\n'
        f"[tools]\nallow = [{allow_str}]\n"
    )
    path = directory / filename
    path.write_text(toml_content)
    return path


class TestLoadAgent:
    def test_valid_agent(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("Do the thing")
        path = _write_agent(
            tmp_path, "coder.toml", "coder", "Writes code",
            "prompt.md", "fast", ["read", "write"],
        )
        agent = load_agent(path)
        assert agent.name == "coder"
        assert agent.description == "Writes code"
        assert agent.prompt == "Do the thing"
        assert agent.category == "fast"
        assert agent.allow == ["read", "write"]

    def test_prompt_with_includes(self, tmp_path: Path) -> None:
        (tmp_path / "header.md").write_text("# Rules")
        (tmp_path / "prompt.md").write_text("{include:header.md}\nDo work")
        path = _write_agent(
            tmp_path, "a.toml", "a", "d", "prompt.md", "c", [],
        )
        agent = load_agent(path)
        assert agent.prompt == "# Rules\nDo work"

    def test_missing_toml_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Agent file not found"):
            load_agent(tmp_path / "missing.toml")

    def test_invalid_schema_raises(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("p")
        toml_path = tmp_path / "bad.toml"
        toml_path.write_text(
            '[agent]\nname = "a"\ndescription = "d"\n'
            'prompt = "prompt.md"\ncategory = "c"\n'
            'extra_field = "bad"\n\n[tools]\nallow = []\n'
        )
        with pytest.raises(ValidationError):
            load_agent(toml_path)

    def test_missing_prompt_file_raises(self, tmp_path: Path) -> None:
        path = _write_agent(
            tmp_path, "a.toml", "a", "d", "missing.md", "c", [],
        )
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            load_agent(path)

    def test_empty_allow_list(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("p")
        path = _write_agent(
            tmp_path, "a.toml", "a", "d", "prompt.md", "c", [],
        )
        agent = load_agent(path)
        assert agent.allow == []

    def test_missing_tools_section_raises(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("p")
        toml_path = tmp_path / "a.toml"
        toml_path.write_text(
            '[agent]\nname = "a"\ndescription = "d"\n'
            'prompt = "prompt.md"\ncategory = "c"\n'
        )
        with pytest.raises(ValueError, match="Missing \\[tools\\] section"):
            load_agent(toml_path)

    def test_missing_allow_key_raises(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("p")
        toml_path = tmp_path / "a.toml"
        toml_path.write_text(
            '[agent]\nname = "a"\ndescription = "d"\n'
            'prompt = "prompt.md"\ncategory = "c"\n\n'
            "[tools]\n"
        )
        with pytest.raises(ValueError, match="Missing 'allow' key"):
            load_agent(toml_path)

    def test_unknown_keys_in_tools_raises(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("p")
        toml_path = tmp_path / "a.toml"
        toml_path.write_text(
            '[agent]\nname = "a"\ndescription = "d"\n'
            'prompt = "prompt.md"\ncategory = "c"\n\n'
            "[tools]\nallow = []\nunknown = true\n"
        )
        with pytest.raises(ValueError, match="Unknown keys in \\[tools\\] section"):
            load_agent(toml_path)

    def test_agent_with_exec_tools(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("Do the thing")
        toml_content = (
            '[agent]\nname = "builder"\ndescription = "Builds"\n'
            'prompt = "prompt.md"\ncategory = "fast"\n\n'
            '[tools]\nallow = ["read", "exec"]\n\n'
            '[[exec]]\nname = "pytest"\nexecutable = "pytest"\n'
            'description = "Run tests"\n\n'
            '[[exec]]\nname = "uv"\nexecutable = "uv"\n'
            'description = "Run uv"\ntimeout_ceiling = 60\n'
        )
        path = tmp_path / "builder.toml"
        path.write_text(toml_content)
        agent = load_agent(path)
        assert len(agent.exec_tools) == 2
        assert agent.exec_tools[0].name == "pytest"
        assert agent.exec_tools[1].name == "uv"
        assert agent.exec_tools[1].timeout_ceiling == 60

    def test_agent_with_shell_config(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("Do the thing")
        toml_content = (
            '[agent]\nname = "sheller"\ndescription = "Shells"\n'
            'prompt = "prompt.md"\ncategory = "fast"\n\n'
            '[tools]\nallow = ["read", "shell"]\n\n'
            '[shell]\nallowed_binaries = ["ls", "cat"]\n'
        )
        path = tmp_path / "sheller.toml"
        path.write_text(toml_content)
        agent = load_agent(path)
        assert agent.shell_config is not None
        assert agent.shell_config.allowed_binaries == ["ls", "cat"]

    def test_agent_without_exec_shell_backward_compat(self, tmp_path: Path) -> None:
        """Agents without [[exec]] or [shell] still load fine."""
        (tmp_path / "prompt.md").write_text("Do the thing")
        path = _write_agent(
            tmp_path, "basic.toml", "basic", "Basic",
            "prompt.md", "fast", ["read"],
        )
        agent = load_agent(path)
        assert agent.exec_tools == []
        assert agent.shell_config is None

    def test_agent_with_provider_model(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("Do the thing")
        toml_content = (
            '[agent]\nname = "direct"\ndescription = "Direct routing"\n'
            'prompt = "prompt.md"\nprovider = "anthropic"\n'
            'model = "claude-sonnet-4-6"\n\n'
            '[tools]\nallow = ["read"]\n'
        )
        path = tmp_path / "direct.toml"
        path.write_text(toml_content)
        agent = load_agent(path)
        assert agent.provider == "anthropic"
        assert agent.model == "claude-sonnet-4-6"
        assert agent.category is None

    def test_self_circular_include_raises(self, tmp_path: Path) -> None:
        (tmp_path / "prompt.md").write_text("{include:prompt.md}")
        path = _write_agent(
            tmp_path, "a.toml", "a", "d", "prompt.md", "c", [],
        )
        with pytest.raises(ValueError, match="Circular include"):
            load_agent(path)


class TestLoadAgents:
    def test_multiple_agents(self, tmp_path: Path) -> None:
        (tmp_path / "p1.md").write_text("prompt1")
        (tmp_path / "p2.md").write_text("prompt2")
        _write_agent(tmp_path, "a.toml", "alpha", "d1", "p1.md", "c", [])
        _write_agent(tmp_path, "b.toml", "beta", "d2", "p2.md", "c", [])
        agents = load_agents(tmp_path)
        assert set(agents.keys()) == {"alpha", "beta"}

    def test_empty_directory(self, tmp_path: Path) -> None:
        agents = load_agents(tmp_path)
        assert agents == {}

    def test_duplicate_names_raises(self, tmp_path: Path) -> None:
        (tmp_path / "p.md").write_text("p")
        _write_agent(tmp_path, "a.toml", "same", "d1", "p.md", "c", [])
        _write_agent(tmp_path, "b.toml", "same", "d2", "p.md", "c", [])
        with pytest.raises(ValueError, match=r"Duplicate agent name.*same"):
            load_agents(tmp_path)

    def test_ignores_non_toml_files(self, tmp_path: Path) -> None:
        (tmp_path / "p.md").write_text("p")
        _write_agent(tmp_path, "a.toml", "alpha", "d", "p.md", "c", [])
        (tmp_path / "readme.md").write_text("not an agent")
        (tmp_path / "notes.txt").write_text("also not an agent")
        agents = load_agents(tmp_path)
        assert set(agents.keys()) == {"alpha"}

    def test_ignores_subdirectories(self, tmp_path: Path) -> None:
        (tmp_path / "p.md").write_text("p")
        _write_agent(tmp_path, "a.toml", "alpha", "d", "p.md", "c", [])
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "p.md").write_text("p")
        _write_agent(subdir, "b.toml", "beta", "d", "p.md", "c", [])
        agents = load_agents(tmp_path)
        assert set(agents.keys()) == {"alpha"}
