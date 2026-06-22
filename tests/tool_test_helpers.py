from __future__ import annotations

from typing import Any

from orxtra.protocols._results import Renderer, ToolOutput
from orxtra.protocols._tool import Tool


class MockRenderer:
    """A renderer that records all render calls for testing."""

    def __init__(self, *, prefix: str = "") -> None:
        self.calls: list[Any] = []
        self._prefix = prefix

    def render(self, data: Any) -> str:  # noqa: ANN401
        self.calls.append(data)
        return f"{self._prefix}{data!s}"


def make_test_tool(
    name: str,
    return_value: str,
    renderer: Renderer[Any] | None = None,
) -> Tool:
    """Create a Tool that returns a fixed string.

    Since Tool.execute currently returns ``str``, this helper simply
    wraps the given ``return_value`` in a coroutine.  When Phase 1.4
    migrates Tool to return ``ToolOutput``, this helper will be updated
    to use ``renderer`` and ``ToolOutput``.  For now ``renderer`` is
    accepted but unused, so callers can be written ahead of time.
    """
    _ = renderer  # Will be used after Tool migration in Phase 1.4

    async def _execute(args: dict[str, Any]) -> str:
        return return_value

    return Tool(
        name=name,
        description=f"Test tool: {name}",
        parameters={},
        execute=_execute,
    )
