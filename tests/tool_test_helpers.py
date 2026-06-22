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
    """Create a Tool that returns a fixed ToolOutput.

    The ``renderer`` parameter is accepted for future use but currently
    unused -- the ``return_value`` string is used directly as both
    ``ToolOutput.text`` and ``ToolOutput.data``.
    """
    _ = renderer  # Reserved for future use

    async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
        return ToolOutput(data=return_value, text=return_value)

    return Tool(
        name=name,
        description=f"Test tool: {name}",
        parameters={},
        execute=_execute,
    )
