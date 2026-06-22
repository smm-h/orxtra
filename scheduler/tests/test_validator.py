from __future__ import annotations

from orxtra.protocols._task import TaskSpec
from orxtra.scheduler._types import WorkflowConfig
from orxtra.scheduler._validator import validate_task_tree


def _agent_task(name: str, **kwargs: object) -> TaskSpec:
    defaults = {
        "agent": "test_agent",
        "task_prompt": "Do something",
        "timeout": 300,
        "context_refinement": True,
    }
    defaults.update(kwargs)
    return TaskSpec(name=name, **defaults)  # type: ignore[arg-type]


def _callable_task(name: str, **kwargs: object) -> TaskSpec:
    defaults = {"callable": "mod:func"}
    defaults.update(kwargs)
    return TaskSpec(name=name, **defaults)  # type: ignore[arg-type]


def _config(
    tasks: list[TaskSpec],
    dependencies: dict[str, list[str]] | None = None,
) -> WorkflowConfig:
    return WorkflowConfig(
        name="test",
        description="Test workflow",
        tasks=tasks,
        dependencies=dependencies or {},
    )


class TestValidateTaskTree:
    def test_valid_workflow(self) -> None:
        config = _config([
            _agent_task("t1"),
            _agent_task("t2"),
        ])
        errors = validate_task_tree(config)
        assert errors == []

    def test_no_execution_mode(self) -> None:
        task = TaskSpec(name="empty")
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("no execution mode" in e for e in errors)

    def test_multiple_execution_modes(self) -> None:
        task = TaskSpec(
            name="dual",
            agent="a",
            task_prompt="p",
            callable="mod:func",
            timeout=300,
            context_refinement=True,
        )
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("multiple execution modes" in e for e in errors)

    def test_agent_missing_timeout(self) -> None:
        task = TaskSpec(
            name="no_timeout",
            agent="a",
            task_prompt="p",
            context_refinement=True,
        )
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("missing 'timeout'" in e for e in errors)

    def test_agent_missing_context_refinement(self) -> None:
        task = TaskSpec(
            name="no_cr",
            agent="a",
            task_prompt="p",
            timeout=300,
        )
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("missing 'context_refinement'" in e for e in errors)

    def test_retry_missing_retry_resume(self) -> None:
        task = _agent_task(
            "retry_task",
            retry=3,
            retry_inject_failure=True,
        )
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("missing 'retry_resume'" in e for e in errors)

    def test_retry_missing_retry_inject_failure(self) -> None:
        task = _agent_task(
            "retry_task",
            retry=3,
            retry_resume=True,
        )
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("missing 'retry_inject_failure'" in e for e in errors)

    def test_for_each_missing_abort_on_failure(self) -> None:
        task = _agent_task(
            "batch",
            for_each="items",
            max_concurrency=5,
        )
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("missing 'for_each_abort_on_failure'" in e for e in errors)

    def test_for_each_missing_max_concurrency(self) -> None:
        task = _agent_task(
            "batch",
            for_each="items",
            for_each_abort_on_failure=True,
        )
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("missing 'max_concurrency'" in e for e in errors)

    def test_duplicate_task_names(self) -> None:
        config = _config([
            _callable_task("dup"),
            _callable_task("dup"),
        ])
        errors = validate_task_tree(config)
        assert any("Duplicate task name 'dup'" in e for e in errors)

    def test_dependency_cycle(self) -> None:
        config = _config(
            [_callable_task("a"), _callable_task("b"), _callable_task("c")],
            dependencies={"a": ["c"], "b": ["a"], "c": ["b"]},
        )
        errors = validate_task_tree(config)
        assert any("cycle" in e.lower() for e in errors)

    def test_variable_name_collision(self) -> None:
        task = _agent_task("research", variables=["research_output"])
        config = _config([task])
        errors = validate_task_tree(config)
        assert any("Variable name collision" in e for e in errors)
        assert any("research_output" in e for e in errors)

    def test_valid_complex_workflow(self) -> None:
        config = _config(
            [
                _callable_task("fetch"),
                _agent_task("analyze"),
                _agent_task(
                    "transform",
                    retry=2,
                    retry_resume=True,
                    retry_inject_failure=True,
                ),
                _agent_task(
                    "batch_process",
                    for_each="items",
                    for_each_abort_on_failure=True,
                    max_concurrency=3,
                ),
                _callable_task("publish"),
            ],
            dependencies={
                "analyze": ["fetch"],
                "transform": ["analyze"],
                "batch_process": ["transform"],
                "publish": ["batch_process"],
            },
        )
        errors = validate_task_tree(config)
        assert errors == []
