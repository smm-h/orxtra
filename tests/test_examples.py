"""Regression tests: every file in examples/ must load through its real loader.

The examples are user-facing documentation of the TOML formats. Each example
is dispatched to its loader based on its top-level structure ([agent],
[workflow], or [categories]), so newly added examples are covered
automatically and format drift fails loudly instead of rotting silently.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from orxtra.agent import Agent, load_agent, load_categories
from orxtra.scheduler import WorkflowConfig, load_workflow

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _example_files() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.toml"))


def test_examples_directory_is_not_empty() -> None:
    assert _example_files(), f"No example TOML files found in {EXAMPLES_DIR}"


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.name)
def test_example_loads(path: Path) -> None:
    data = tomllib.loads(path.read_text())
    if "workflow" in data:
        workflow = load_workflow(path)
        assert isinstance(workflow, WorkflowConfig)
        assert workflow.tasks
    elif "categories" in data:
        categories = load_categories(path)
        assert categories
        assert all(
            isinstance(model, str) for model in categories.values()
        )
    elif "agent" in data:
        agent = load_agent(path)
        assert isinstance(agent, Agent)
        assert agent.prompt
    else:
        pytest.fail(
            f"{path.name} has no [agent], [workflow], or [categories]"
            " section -- cannot dispatch to a loader. Add the section or"
            " extend this test for the new example format.",
        )
