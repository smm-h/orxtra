from __future__ import annotations

import pytest
from orxtra.protocols import Tool, ToolError
from orxtra.tool import CONSULT_STRIP_TOOLS, FILE_MUTATION_TOOLS, wrap_tools_for_session


async def _noop(args: dict[str, object]) -> str:
    return "ok"


def _make_tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=name.capitalize(),
        parameters={"type": "object", "properties": {}},
        execute=_noop,
    )


class TestE2ESecurity:
    async def test_consult_strips_write_tool(self) -> None:
        assert "write" in CONSULT_STRIP_TOOLS

    async def test_consult_strips_edit_tool(self) -> None:
        assert "edit" in CONSULT_STRIP_TOOLS

    async def test_consult_strips_delete_tool(self) -> None:
        assert "delete" in CONSULT_STRIP_TOOLS

    async def test_consult_strips_git_tool(self) -> None:
        assert "git" in CONSULT_STRIP_TOOLS

    async def test_consult_preserves_read_tool(self) -> None:
        assert "read" not in CONSULT_STRIP_TOOLS

    async def test_consult_strips_lifecycle_tools(self) -> None:
        lifecycle_tools = {
            "start_task", "end_task", "create_task",
            "create_workflow", "create_wait_for",
        }
        for name in lifecycle_tools:
            assert name in CONSULT_STRIP_TOOLS, (
                f"{name!r} missing from CONSULT_STRIP_TOOLS"
            )

    async def test_consult_filtering_applied_to_tool_registry(self) -> None:
        registry = {
            name: _make_tool(name)
            for name in [
                "read", "write", "edit", "delete", "git",
                "exec", "http", "search", "start_task", "end_task",
            ]
        }

        filtered = {
            name: tool for name, tool in registry.items()
            if name not in CONSULT_STRIP_TOOLS
        }

        # Safe tools survive.
        assert "read" in filtered
        assert "search" in filtered
        # Dangerous tools are removed.
        assert "write" not in filtered
        assert "edit" not in filtered
        assert "delete" not in filtered
        assert "git" not in filtered
        assert "exec" not in filtered
        assert "http" not in filtered
        assert "start_task" not in filtered
        assert "end_task" not in filtered

    async def test_file_mutation_tools_subset_of_consult_strip(self) -> None:
        assert FILE_MUTATION_TOOLS.issubset(CONSULT_STRIP_TOOLS), (
            f"FILE_MUTATION_TOOLS has entries not in CONSULT_STRIP_TOOLS: "
            f"{FILE_MUTATION_TOOLS - CONSULT_STRIP_TOOLS}"
        )

    async def test_tools_require_active_task(self) -> None:
        tool = _make_tool("test_tool")

        def reject_all(session_id: str) -> None:
            msg = f"No active task for session {session_id!r}"
            raise ToolError(msg)

        wrapped = wrap_tools_for_session(
            tools=[tool],
            scheduler_check=reject_all,  # type: ignore[arg-type]
            secret_registry=None,
            trace_callback=None,
            session_id="test-session",
        )

        with pytest.raises(ToolError, match="No active task"):
            await wrapped[0].execute({})
