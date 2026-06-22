from __future__ import annotations

import pytest
from orxtra.scheduler._types import (
    AgentExecution,
    AttemptSummary,
    EscalationPayload,
    Execution,
    ScriptExecution,
    ServiceConfig,
    TaskContext,
    TaskResult,
    TaskSpec,
    TaskState,
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


class TestProtocolReexports:
    def test_reexports_work(self) -> None:
        assert TaskContext is not None
        assert TaskResult is not None
        assert AttemptSummary is not None
        assert EscalationPayload is not None
        assert TaskSpec is not None
        assert TaskState is not None
        assert Execution is not None
        assert AgentExecution is not None
        assert ScriptExecution is not None

    def test_task_state_values(self) -> None:
        assert TaskState.CREATED == "created"
        assert TaskState.ACTIVE == "active"
        assert TaskState.COMPLETED == "completed"
