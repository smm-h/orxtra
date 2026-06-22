from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import uuid6
from conftest import MockTraceWriter
from orxtra.overseer._autonomy import AutonomyLevel
from orxtra.overseer._health import HealthMonitor
from orxtra.overseer._overseer import Overseer, load_overseer_prompt
from orxtra.protocols._tool import Tool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
    from uuid import UUID


class MockSession:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0
        self.turn_count: int = 0

    @property
    def model(self) -> str:
        return "test-model"

    @property
    def system_prompt(self) -> str:
        return "test-prompt"

    @property
    def tools(self) -> list[Any]:
        return []

    @property
    def session_id(self) -> str | None:
        return "test-session-id"

    async def send(
        self, message: str,
    ) -> AsyncIterator[Any]:
        self.sent_messages.append(message)
        return
        yield

    def resume_id(self) -> str:
        return "test-session-id"


async def _noop_execute(args: dict[str, Any]) -> str:
    return "ok"


def _make_tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"Mock {name} tool",
        parameters={"type": "object", "properties": {}},
        execute=_noop_execute,
    )


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


def test_set_extra_tools_adds_tools(overseer: Overseer) -> None:
    """Extra tools passed via set_extra_tools appear in get_tools."""
    tools_before = overseer.get_tools()
    assert len(tools_before) == 13

    extra = [_make_tool("alpha"), _make_tool("beta")]
    overseer.set_extra_tools(extra)

    tools_after = overseer.get_tools()
    names = {t.name for t in tools_after}
    assert "alpha" in names
    assert "beta" in names
    assert len(tools_after) == 15


def test_lifecycle_tools_present_after_injection(
    overseer: Overseer,
) -> None:
    """All 6 lifecycle tool names appear after injection."""
    lifecycle_names = [
        "start_task",
        "end_task",
        "create_task",
        "create_workflow",
        "create_wait_for",
        "await_task",
    ]
    overseer.set_extra_tools(
        [_make_tool(n) for n in lifecycle_names],
    )

    result_names = {t.name for t in overseer.get_tools()}
    for name in lifecycle_names:
        assert name in result_names


def test_consult_tool_present_after_injection(
    overseer: Overseer,
) -> None:
    """A consult tool injected via set_extra_tools is retrievable."""
    overseer.set_extra_tools([_make_tool("consult")])

    result_names = {t.name for t in overseer.get_tools()}
    assert "consult" in result_names


def test_get_tools_returns_all_tools_with_extras(
    overseer: Overseer,
) -> None:
    """Total count is 13 base + 7 extra = 20."""
    lifecycle_names = [
        "start_task",
        "end_task",
        "create_task",
        "create_workflow",
        "create_wait_for",
        "await_task",
    ]
    extra = [_make_tool(n) for n in lifecycle_names]
    extra.append(_make_tool("consult"))
    overseer.set_extra_tools(extra)

    tools = overseer.get_tools()
    assert len(tools) == 20


def test_set_extra_tools_replaces_previous(
    overseer: Overseer,
) -> None:
    """Calling set_extra_tools twice keeps only the second set."""
    overseer.set_extra_tools(
        [_make_tool("first_a"), _make_tool("first_b")],
    )
    overseer.set_extra_tools(
        [_make_tool("second_x")],
    )

    names = {t.name for t in overseer.get_tools()}
    assert "second_x" in names
    assert "first_a" not in names
    assert "first_b" not in names
    assert len(overseer.get_tools()) == 14


def test_load_overseer_prompt_returns_nonempty() -> None:
    """load_overseer_prompt returns a non-empty string with 'Overseer'."""
    prompt = load_overseer_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "Overseer" in prompt
