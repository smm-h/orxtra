from __future__ import annotations

import pytest
from orxtra.protocols import TaskSpec
from orxtra.scheduler._types import (
    ServiceConfig,
    WorkflowConfig,
)
from pydantic import ValidationError


class TestWorkflowConfig:
    def test_accepts_valid_data(self) -> None:
        task = TaskSpec(
            name="t1",
            agent="researcher",
            task_prompt="Do research",
            timeout=300,
            context_refinement=True,
        )
        config = WorkflowConfig(
            name="test",
            description="A test workflow",
            tasks=[task],
            dependencies={},
        )
        assert config.name == "test"
        assert config.description == "A test workflow"
        assert len(config.tasks) == 1
        assert config.dependencies == {}

    def test_rejects_extra_fields(self) -> None:
        task = TaskSpec(
            name="t1",
            agent="a",
            task_prompt="p",
            timeout=300,
            context_refinement=True,
        )
        with pytest.raises(ValidationError):
            WorkflowConfig(
                name="test",
                description="desc",
                tasks=[task],
                dependencies={},
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_rejects_missing_name(self) -> None:
        task = TaskSpec(
            name="t1",
            agent="a",
            task_prompt="p",
            timeout=300,
            context_refinement=True,
        )
        with pytest.raises(ValidationError):
            WorkflowConfig(
                description="desc",
                tasks=[task],
                dependencies={},
            )  # type: ignore[call-arg]

    def test_rejects_missing_description(self) -> None:
        task = TaskSpec(
            name="t1",
            agent="a",
            task_prompt="p",
            timeout=300,
            context_refinement=True,
        )
        with pytest.raises(ValidationError):
            WorkflowConfig(
                name="test",
                tasks=[task],
                dependencies={},
            )  # type: ignore[call-arg]


class TestServiceConfig:
    def test_accepts_valid_data(self) -> None:
        config = ServiceConfig(
            name="postgres",
            start_command="pg_ctl start",
            stop_command="pg_ctl stop",
            health_check_command="pg_isready",
            port=5432,
        )
        assert config.name == "postgres"
        assert config.port == 5432

    def test_defaults(self) -> None:
        config = ServiceConfig(
            name="svc",
            start_command="start",
            stop_command="stop",
        )
        assert config.ready_timeout == 30
        assert config.health_check_command is None
        assert config.port is None
