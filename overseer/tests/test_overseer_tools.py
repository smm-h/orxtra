from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import uuid6
from .conftest import MockSession, MockTraceWriter
from orxtra.overseer._autonomy import AutonomyLevel
from orxtra.overseer._health import HealthMonitor
from orxtra.overseer._overseer import Overseer
from orxtra.protocols import Tool

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


async def _noop(args: dict[str, object]) -> str:
    return "{}"


def _make_mock_tool(name: str) -> Tool:
    return Tool(
        name=name,
        description="test",
        parameters={},
        execute=_noop,
    )


def test_get_tools_returns_all_categories(
    overseer: Overseer,
) -> None:
    tools = overseer.get_tools()
    # 6 memory + 6 file + 1 notepad = 13
    assert len(tools) == 13


def test_file_tools_present(overseer: Overseer) -> None:
    tools = overseer.get_tools()
    names = {t.name for t in tools}
    for expected in (
        "read", "list_dir", "grep", "glob", "stat", "diff",
    ):
        assert expected in names, f"missing file tool: {expected}"


def test_notepad_tool_present(overseer: Overseer) -> None:
    tools = overseer.get_tools()
    names = {t.name for t in tools}
    assert "notepad" in names


def test_memory_tools_present(overseer: Overseer) -> None:
    tools = overseer.get_tools()
    names = {t.name for t in tools}
    for expected in (
        "record_decision",
        "add_constraint",
        "record_assumption",
        "create_inbox_item",
        "write_lesson",
        "update_workflow_status",
    ):
        assert expected in names, f"missing memory tool: {expected}"


def test_extra_tools_injected(
    session: MockSession,
    tw: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    mock = _make_mock_tool("custom_action")
    ov = Overseer(
        session=session,  # type: ignore[arg-type]
        trace_writer=tw,  # type: ignore[arg-type]
        run_id=run_id,
        autonomy_level=AutonomyLevel.MEDIUM,
        health_monitor=HealthMonitor(),
        read_root=tmp_path,
        extra_tools=[mock],
    )
    tools = ov.get_tools()
    names = {t.name for t in tools}
    assert "custom_action" in names
    assert len(tools) == 14  # 13 base + 1 extra


def test_extra_tools_default_empty(
    overseer: Overseer,
) -> None:
    tools = overseer.get_tools()
    # No extra tools, should be exactly 13
    assert len(tools) == 13


@pytest.mark.asyncio
async def test_read_root_used_by_file_tools(
    session: MockSession,
    tw: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello world")
    ov = Overseer(
        session=session,  # type: ignore[arg-type]
        trace_writer=tw,  # type: ignore[arg-type]
        run_id=run_id,
        autonomy_level=AutonomyLevel.MEDIUM,
        health_monitor=HealthMonitor(),
        read_root=tmp_path,
    )
    tools = ov.get_tools()
    read_tool = next(t for t in tools if t.name == "read")
    result = (await read_tool.execute({"path": "hello.txt"})).text
    assert "hello world" in result


def test_lifecycle_tools_via_extra(
    session: MockSession,
    tw: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    lifecycle_tools = [
        _make_mock_tool("start_task"),
        _make_mock_tool("end_task"),
        _make_mock_tool("create_task"),
    ]
    ov = Overseer(
        session=session,  # type: ignore[arg-type]
        trace_writer=tw,  # type: ignore[arg-type]
        run_id=run_id,
        autonomy_level=AutonomyLevel.MEDIUM,
        health_monitor=HealthMonitor(),
        read_root=tmp_path,
        extra_tools=lifecycle_tools,
    )
    tools = ov.get_tools()
    names = {t.name for t in tools}
    assert "start_task" in names
    assert "end_task" in names
    assert "create_task" in names
    assert len(tools) == 16  # 13 base + 3 extra
