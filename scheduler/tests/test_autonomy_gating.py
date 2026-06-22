"""Tests for OverseerAdapter autonomy gating.

Verifies that gate_tools and _gate_tool correctly
block or allow tool execution based on the current
AutonomyLevel and the tool's action type from
TOOL_ACTION_TYPES.

The scheduler does not depend on orxtra.overseer at
runtime (TYPE_CHECKING only). The _gate_tool method
imports is_autonomous locally. We inject the autonomy
module into sys.modules so the local import resolves
without installing the full overseer package.
"""

from __future__ import annotations

import sys
import types
from enum import StrEnum
from typing import Any

# Inject a minimal orxtra.overseer._autonomy module so
# that _gate_tool's local import resolves. Must happen
# before importing OverseerAdapter.
_ALL_SENTINEL = "__all__"


class AutonomyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


_AUTONOMY_RULES: dict[AutonomyLevel, set[str]] = {
    AutonomyLevel.LOW: {"read_only"},
    AutonomyLevel.MEDIUM: {
        "read_only",
        "retry",
        "budget_reallocation",
        "concurrency",
        "task_assumption",
    },
    AutonomyLevel.HIGH: {
        "read_only",
        "retry",
        "budget_reallocation",
        "concurrency",
        "task_assumption",
        "scope_change",
        "architecture_decision",
        "understanding_assumption",
    },
    AutonomyLevel.MAX: {_ALL_SENTINEL},
}


def _is_autonomous(
    level: AutonomyLevel, action_type: str,
) -> bool:
    allowed = _AUTONOMY_RULES[level]
    if _ALL_SENTINEL in allowed:
        return True
    return action_type in allowed


_mod = types.ModuleType("orxtra.overseer._autonomy")
_mod.AutonomyLevel = AutonomyLevel  # type: ignore[attr-defined]
_mod.is_autonomous = _is_autonomous  # type: ignore[attr-defined]

# Ensure parent packages exist in sys.modules
if "orxtra.overseer" not in sys.modules:
    _parent = types.ModuleType("orxtra.overseer")
    sys.modules["orxtra.overseer"] = _parent
sys.modules["orxtra.overseer._autonomy"] = _mod

from orxtra.protocols._tool import Tool  # noqa: E402
from orxtra.scheduler._overseer import (  # noqa: E402
    OverseerAdapter,
)

# -- Helpers ----------------------------------------------


async def _dummy_execute(
    args: dict[str, Any],
) -> str:
    _ = args
    return "executed"


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

        result = await gated.execute({})

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

        result = await gated.execute({})

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
        result = await gated.execute({})
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
            result = await tool_map[name].execute({})
            assert "blocked" in result.lower(), (
                f"{name} should be blocked at LOW"
            )
            assert "scope_change" in result

        # Verify read tool is not gated
        read_result = await tool_map["read"].execute(
            {},
        )
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
        result = await tool.execute({})
        assert "blocked" in result.lower()
