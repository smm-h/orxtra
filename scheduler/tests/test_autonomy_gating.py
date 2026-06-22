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
