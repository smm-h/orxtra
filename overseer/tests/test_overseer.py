from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import uuid6
from .conftest import MockSession, MockTraceWriter
from orxtra.overseer._autonomy import AutonomyLevel
from orxtra.overseer._health import HealthMonitor
from orxtra.overseer._overseer import Overseer
from orxtra.protocols import (
    CheckResult,
    EscalationPayload,
    RunStarted,
    TaskContext,
    TaskFailed,
)

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID


@pytest.fixture
def run_id() -> UUID:
    return uuid6.uuid7()


@pytest.fixture
def tw() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def session() -> MockSession:
    return MockSession()


@pytest.fixture
def overseer(
    session: MockSession,
    tw: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> Overseer:
    return Overseer(
        session=session,  # type: ignore[arg-type]
        trace_writer=tw,  # type: ignore[arg-type]
        run_id=run_id,
        autonomy_level=AutonomyLevel.MEDIUM,
        health_monitor=HealthMonitor(),
        read_root=tmp_path,
    )


def test_prepare_event_returns_formatted_message(
    overseer: Overseer,
) -> None:
    event = RunStarted(
        intent="Build feature", config_snapshot={},
    )
    result = overseer.prepare_event(event)
    parsed = json.loads(result)
    assert parsed["event_type"] == "RunStarted"


def test_get_tools_returns_all(overseer: Overseer) -> None:
    tools = overseer.get_tools()
    assert len(tools) == 13
    names = {t.name for t in tools}
    assert "record_decision" in names
    assert "add_constraint" in names
    assert "record_assumption" in names
    assert "create_inbox_item" in names
    assert "write_lesson" in names
    assert "update_workflow_status" in names


def test_overseer_construction(
    session: MockSession,
    tw: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    overseer = Overseer(
        session=session,  # type: ignore[arg-type]
        trace_writer=tw,  # type: ignore[arg-type]
        run_id=run_id,
        autonomy_level=AutonomyLevel.HIGH,
        health_monitor=HealthMonitor(threshold=0.5),
        read_root=tmp_path,
    )
    assert overseer._autonomy_level == AutonomyLevel.HIGH  # noqa: SLF001


def test_prepare_run_started(
    overseer: Overseer,
) -> None:
    event = RunStarted(
        intent="Implement module",
        config_snapshot={"key": "val"},
    )
    result = overseer.prepare_event(event)
    parsed = json.loads(result)
    assert parsed["event_type"] == "RunStarted"
    assert parsed["intent"] == "Implement module"


def test_prepare_task_failed(
    overseer: Overseer,
) -> None:
    task_id = uuid6.uuid7()
    ctx = TaskContext(
        variables={},
        run_id=uuid6.uuid7(),
        task_name="run_tests",
        task_id=task_id,
        attempt=1,
        prior_attempts=None,
        notepad_content="",
        parent_task_id=None,
        nesting_depth=0,
    )
    payload = EscalationPayload(
        task_name="run_tests",
        task_id=task_id,
        agent_name="test-agent",
        attempts=1,
        failed_checks=[
            CheckResult(
                passed=False, message="assertion failed",
            ),
        ],
        agent_summary="Test assertion failed",
        context=ctx,
    )
    event = TaskFailed(
        task_id=task_id,
        task_name="run_tests",
        payload=payload,
    )
    result = overseer.prepare_event(event)
    parsed = json.loads(result)
    assert parsed["event_type"] == "TaskFailed"
    assert parsed["task_name"] == "run_tests"


@pytest.mark.asyncio
async def test_tools_are_functional(
    overseer: Overseer, tw: MockTraceWriter,
) -> None:
    tools = overseer.get_tools()
    decision_tool = next(
        t for t in tools if t.name == "record_decision"
    )
    result = (await decision_tool.execute({
        "decision_type": "test",
        "choice": {"key": "value"},
    })).text
    parsed = json.loads(result)
    assert "decision_id" in parsed
    assert len(tw.calls) == 1
