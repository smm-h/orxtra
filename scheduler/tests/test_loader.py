from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxtra.protocols._execution import AgentExecution, ScriptExecution
from orxtra.scheduler._loader import load_workflow

if TYPE_CHECKING:
    from pathlib import Path


class TestLoadWorkflow:
    def test_minimal_valid_workflow(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "workflow.toml"
        toml_file.write_text("""
[workflow]
name = "minimal"
description = "A minimal workflow"

[[tasks]]
name = "task1"
agent = "researcher"
task_prompt = "Research things"
timeout = 300
context_refinement = true
""")
        config = load_workflow(toml_file)
        assert config.name == "minimal"
        assert config.description == "A minimal workflow"
        assert len(config.tasks) == 1
        assert config.tasks[0].name == "task1"
        assert config.dependencies == {}

    def test_workflow_with_dependencies(
        self, tmp_path: Path
    ) -> None:
        toml_file = tmp_path / "workflow.toml"
        toml_file.write_text("""
[workflow]
name = "pipeline"
description = "Pipeline with deps"

[[tasks]]
name = "research"
agent = "researcher"
task_prompt = "Do research"
timeout = 300
context_refinement = true

[[tasks]]
name = "generate"
agent = "writer"
task_prompt = "Write based on research"
timeout = 600
context_refinement = false

[dependencies]
generate = ["research"]
""")
        config = load_workflow(toml_file)
        assert len(config.tasks) == 2
        research = config.tasks[0]
        generate = config.tasks[1]
        assert research.depends_on is None
        assert generate.depends_on == ["research"]

    def test_workflow_with_postchecks(
        self, tmp_path: Path
    ) -> None:
        toml_file = tmp_path / "workflow.toml"
        toml_file.write_text("""
[workflow]
name = "checked"
description = "Workflow with postchecks"

[[tasks]]
name = "impl"
agent = "coder"
task_prompt = "Implement"
timeout = 300
context_refinement = true

[tasks.postchecks]
scripts = ["module:check_types"]
agents = [{agent = "reviewer", task = "Code review", block_threshold = "minor"}]
""")
        config = load_workflow(toml_file)
        task = config.tasks[0]
        assert len(task.postchecks) == 2
        assert isinstance(task.postchecks[0], ScriptExecution)
        assert task.postchecks[0].callable == "module:check_types"
        assert isinstance(task.postchecks[1], AgentExecution)
        assert task.postchecks[1].agent == "reviewer"
        assert task.postchecks[1].task == "Code review"
        assert task.postchecks[1].block_threshold.value == "minor"

    def test_load_from_string(self) -> None:
        toml_str = """
[workflow]
name = "inline"
description = "Loaded from string"

[[tasks]]
name = "task1"
callable = "module:run"
"""
        config = load_workflow(toml_str)
        assert config.name == "inline"
        assert config.tasks[0].callable == "module:run"

    def test_missing_workflow_section(self) -> None:
        toml_str = """
[[tasks]]
name = "task1"
callable = "module:run"
"""
        with pytest.raises(
            ValueError, match="Missing \\[workflow\\] section"
        ):
            load_workflow(toml_str)

    def test_missing_name(self) -> None:
        toml_str = """
[workflow]
description = "no name"

[[tasks]]
name = "task1"
callable = "module:run"
"""
        with pytest.raises(ValueError, match="Missing 'name'"):
            load_workflow(toml_str)

    def test_empty_tasks(self) -> None:
        toml_str = """
[workflow]
name = "empty"
description = "no tasks"
"""
        with pytest.raises(ValueError, match="at least one task"):
            load_workflow(toml_str)

    def test_dependency_references_nonexistent_task(self) -> None:
        toml_str = """
[workflow]
name = "bad"
description = "bad deps"

[[tasks]]
name = "task1"
callable = "module:run"

[dependencies]
task1 = ["nonexistent"]
"""
        with pytest.raises(
            ValueError, match="Dependency target 'nonexistent'"
        ):
            load_workflow(toml_str)

    def test_dependency_source_nonexistent(self) -> None:
        toml_str = """
[workflow]
name = "bad"
description = "bad source"

[[tasks]]
name = "task1"
callable = "module:run"

[dependencies]
ghost = ["task1"]
"""
        with pytest.raises(
            ValueError, match="Dependency source 'ghost'"
        ):
            load_workflow(toml_str)

    def test_complex_workflow(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "complex.toml"
        toml_file.write_text("""
[workflow]
name = "complex"
description = "Five tasks, mixed deps"

[[tasks]]
name = "fetch_data"
callable = "data:fetch"

[[tasks]]
name = "analyze"
agent = "analyst"
task_prompt = "Analyze data"
timeout = 600
context_refinement = true

[[tasks]]
name = "summarize"
agent = "writer"
task_prompt = "Write summary"
timeout = 300
context_refinement = false

[[tasks]]
name = "review"
agent = "reviewer"
task_prompt = "Review output"
timeout = 300
context_refinement = true

[[tasks]]
name = "publish"
callable = "publish:deploy"

[dependencies]
analyze = ["fetch_data"]
summarize = ["analyze"]
review = ["summarize"]
publish = ["review"]
""")
        config = load_workflow(toml_file)
        assert len(config.tasks) == 5
        assert config.tasks[0].depends_on is None
        assert config.tasks[1].depends_on == ["fetch_data"]
        assert config.tasks[4].depends_on == ["review"]

    def test_task_with_subtasks(self) -> None:
        toml_str = """
[workflow]
name = "nested"
description = "Has subtasks"

[[tasks]]
name = "parent"
[[tasks.subtasks]]
name = "child1"
callable = "mod:func1"
[[tasks.subtasks]]
name = "child2"
callable = "mod:func2"
"""
        config = load_workflow(toml_str)
        parent = config.tasks[0]
        assert parent.subtasks is not None
        assert len(parent.subtasks) == 2
        assert parent.subtasks[0].name == "child1"
        assert parent.subtasks[1].name == "child2"

    def test_postchecks_scripts_only(self) -> None:
        toml_str = """
[workflow]
name = "scripts-only"
description = "Postchecks with only scripts"

[[tasks]]
name = "task1"
callable = "mod:run"

[tasks.postchecks]
scripts = ["mod:check1", "mod:check2"]
"""
        config = load_workflow(toml_str)
        assert len(config.tasks[0].postchecks) == 2
        assert all(
            isinstance(p, ScriptExecution)
            for p in config.tasks[0].postchecks
        )

    def test_postchecks_agents_only(self) -> None:
        toml_str = """
[workflow]
name = "agents-only"
description = "Postchecks with only agents"

[[tasks]]
name = "task1"
callable = "mod:run"

[tasks.postchecks]
agents = [
    {agent = "reviewer1", task = "Review", block_threshold = "major"},
    {agent = "reviewer2", task = "Second review", block_threshold = "critical"},
]
"""
        config = load_workflow(toml_str)
        assert len(config.tasks[0].postchecks) == 2
        assert all(
            isinstance(p, AgentExecution)
            for p in config.tasks[0].postchecks
        )
