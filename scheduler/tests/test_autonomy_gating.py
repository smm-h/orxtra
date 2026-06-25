"""Tests for OverseerAdapter autonomy gating.

Verifies that gate_tools and _gate_tool correctly
block or allow tool execution based on the current
AutonomyLevel and the tool's action type from
TOOL_ACTION_TYPES.
"""

from __future__ import annotations

from typing import Any

from orxtra.protocols import AutonomyLevel, Tool
from orxtra.scheduler._overseer import OverseerAdapter

# -- Helpers ----------------------------------------------


async def _dummy_execute(
    args: dict[str, Any],
) -> Any:  # noqa: ANN401
    from orxtra.protocols import Confirmation, ToolOutput  # noqa: PLC0415
    _ = args
    return ToolOutput(data=Confirmation(message="executed"), text="executed")


def _make_tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"Test {name}",
        parameters={"type": "object", "properties": {}},
        execute=_dummy_execute,
    )


def _make_adapter(
    level: AutonomyLevel,
) -> OverseerAdapter:
    """Create an OverseerAdapter with bypassed __init__
    and the given autonomy level."""
    adapter = OverseerAdapter.__new__(OverseerAdapter)
    adapter._autonomy_level = level  # noqa: SLF001
    return adapter


class _MockSession:
    """Minimal session stand-in with mutable _tools."""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools

    @property
    def tools(self) -> list[Tool]:
        return self._tools


class _MockOverseer:
    """Minimal Overseer stand-in exposing a session."""

    def __init__(self, session: _MockSession) -> None:
        self._session = session

    @property
    def session(self) -> _MockSession:
        return self._session

    @session.setter
    def session(self, value: _MockSession) -> None:
        self._session = value


class _MockHealthMonitor:
    """Minimal HealthMonitor stand-in."""

    def record_event(
        self,
        event_type: str,
        success: bool,
        is_repetition: bool = False,
    ) -> None:
        pass

    def is_degraded(self, event_type: str) -> bool:
        return False


# -- Tests ------------------------------------------------


class TestAutonomyGating:
    """gate_tools wraps or passes through based on
    autonomy level and action type."""

    async def test_low_autonomy_blocks_create_workflow(
        self,
    ) -> None:
        adapter = _make_adapter(AutonomyLevel.LOW)
        tool = _make_tool("create_workflow")
        [gated] = adapter.gate_tools([tool])

        result = (await gated.execute({})).text
        assert "blocked" in result.lower()
        assert "scope_change" in result

    async def test_max_autonomy_allows_everything(
        self,
    ) -> None:
        adapter = _make_adapter(AutonomyLevel.MAX)
        tool = _make_tool("create_workflow")
        [gated] = adapter.gate_tools([tool])

        assert gated.execute is tool.execute

    async def test_blocked_action_returns_message(
        self,
    ) -> None:
        adapter = _make_adapter(AutonomyLevel.LOW)
        tool = _make_tool("create_task")
        [gated] = adapter.gate_tools([tool])

        result = (await gated.execute({})).text
        assert "blocked" in result.lower()
        assert "scope_change" in result

    async def test_allowed_action_executes_normally(
        self,
    ) -> None:
        adapter = _make_adapter(AutonomyLevel.MEDIUM)
        tool = _make_tool("start_task")
        [gated] = adapter.gate_tools([tool])

        # start_task is "retry" action, allowed at MEDIUM
        assert gated.execute is tool.execute
        result = (await gated.execute({})).text
        assert result == "executed"


class TestSessionToolsGating:
    """OverseerAdapter gates session tools at
    construction and after session update."""

    async def test_low_autonomy_gates_scope_change_on_session(
        self,
    ) -> None:
        """With LOW autonomy, scope_change tools
        (create_workflow, create_task, create_wait_for)
        are gated on the Overseer's session."""
        scope_tools = [
            _make_tool("create_workflow"),
            _make_tool("create_task"),
            _make_tool("create_wait_for"),
        ]
        read_tool = _make_tool("read")
        session = _MockSession(
            scope_tools + [read_tool],
        )
        overseer = _MockOverseer(session)
        monitor = _MockHealthMonitor()

        adapter = OverseerAdapter(
            overseer=overseer,  # type: ignore[arg-type]
            health_monitor=monitor,  # type: ignore[arg-type]
            autonomy_level=AutonomyLevel.LOW,
        )

        # Verify scope_change tools are gated
        tool_map = {
            t.name: t for t in session.tools
        }
        for name in (
            "create_workflow",
            "create_task",
            "create_wait_for",
        ):
            result = (await tool_map[name].execute({})).text
            assert "blocked" in result.lower(), (
                f"{name} should be blocked at LOW"
            )
            assert "scope_change" in result

        # Verify read tool is not gated
        read_result = (await tool_map["read"].execute(
            {},
        )).text
        assert read_result == "executed"

    async def test_max_autonomy_does_not_gate_session(
        self,
    ) -> None:
        """With MAX autonomy, all tools pass through
        ungated."""
        tools = [
            _make_tool("create_workflow"),
            _make_tool("read"),
        ]
        session = _MockSession(list(tools))
        overseer = _MockOverseer(session)
        monitor = _MockHealthMonitor()

        _ = OverseerAdapter(
            overseer=overseer,  # type: ignore[arg-type]
            health_monitor=monitor,  # type: ignore[arg-type]
            autonomy_level=AutonomyLevel.MAX,
        )

        # All tools should have original execute
        for original, current in zip(
            tools, session.tools, strict=True,
        ):
            assert current.execute is original.execute

    async def test_update_session_gates_new_tools(
        self,
    ) -> None:
        """After update_session, the new session's tools
        are also gated."""
        old_session = _MockSession([_make_tool("read")])
        overseer = _MockOverseer(old_session)
        monitor = _MockHealthMonitor()

        adapter = OverseerAdapter(
            overseer=overseer,  # type: ignore[arg-type]
            health_monitor=monitor,  # type: ignore[arg-type]
            autonomy_level=AutonomyLevel.LOW,
        )

        # Create a new session with a scope_change tool
        new_session = _MockSession(
            [_make_tool("create_workflow")],
        )
        adapter.update_session(new_session)  # type: ignore[arg-type]

        [tool] = new_session.tools
        result = (await tool.execute({})).text
        assert "blocked" in result.lower()

