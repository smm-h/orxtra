"""Tests for the monty and command execution type factories (data-defined tools).

Covers:
- Monty tool with file write capability through write-safety (stale-tracking).
- Monty tool with command capability (subprocess with arg validation).
- Infinite loop terminated by max_duration_secs.
- Ungranted capability hard-errors.
- Readonly-only tool carries the readonly tag.
- Mutation capability tool carries the mutation tag.
- ToolError from a capability propagates to the script.
- Output schema validation.
- Command execution type factory.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from orxtra.protocols import ToolError
from orxtra.tool._data_tool_monty import (
    _derive_tags,
    build_command_tool,
    build_monty_tool,
)
from orxtra.tool._data_tool_types import (
    CommandExecution,
    DataToolDefinition,
    MontyExecution,
    OutputConfig,
    ParamDef,
    ResourceLimits,
)
from orxtra.write_safety import StaleWriteTracker, WriteQueue


# ---------------------------------------------------------------------------
# Minimal ToolDeps stub
# ---------------------------------------------------------------------------


@dataclass
class _StubToolDeps:
    """Minimal stub for ToolDeps used in tests.

    Provides the fields that capability builders access.
    """

    read_root: Path
    write_scope: list[Path] | None = None
    write_queue: WriteQueue = field(default_factory=WriteQueue)
    stale_tracker: StaleWriteTracker = field(
        default_factory=StaleWriteTracker,
    )
    session_id: str = "test-session"
    trace_writer: Any = field(default_factory=MagicMock)
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    task_id: uuid.UUID = field(default_factory=uuid.uuid4)
    task_name: str = "test-task"
    task_agent: str = "test-agent"
    scheduler_ref: Any = field(default_factory=MagicMock)
    transport_registry: dict[str, Any] = field(default_factory=dict)
    categories: dict[str, str] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)
    preview_threshold: int = 50000
    preview_lines: int = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monty_definition(
    *,
    name: str = "test_monty",
    description: str = "A test monty tool",
    namespace: str = "custom.test",
    code: str = "result = 42\nresult",
    capabilities: list[str] | None = None,
    max_duration_secs: int = 5,
    params: dict[str, ParamDef] | None = None,
    output_schema: dict[str, Any] | None = None,
    deferred: bool = False,
    tags: list[str] | None = None,
) -> DataToolDefinition:
    """Build a DataToolDefinition with MontyExecution for testing."""
    execution = MontyExecution(
        type="monty",
        code=code,
        capabilities=capabilities or [],
        limits=ResourceLimits(max_duration_secs=max_duration_secs),
    )
    output = (
        OutputConfig(schema_=output_schema) if output_schema else None
    )
    return DataToolDefinition(
        name=name,
        description=description,
        namespace=namespace,
        deferred=deferred,
        tags=tags,
        params=params or {},
        execution=execution,
        output=output,
    )


def _make_command_definition(
    *,
    name: str = "test_command",
    description: str = "A test command tool",
    namespace: str = "custom.test",
    executable: str = "echo",
    arg_validation: bool = True,
    timeout_ceiling: int = 10,
    params: dict[str, ParamDef] | None = None,
    output_schema: dict[str, Any] | None = None,
    deferred: bool = False,
    tags: list[str] | None = None,
) -> DataToolDefinition:
    """Build a DataToolDefinition with CommandExecution for testing."""
    execution = CommandExecution(
        type="command",
        executable=executable,
        arg_validation=arg_validation,
        timeout_ceiling=timeout_ceiling,
    )
    output = (
        OutputConfig(schema_=output_schema) if output_schema else None
    )
    if params is None:
        params = {
            "args": ParamDef(
                type="string",
                description="Command arguments",
                required=False,
            ),
        }
    return DataToolDefinition(
        name=name,
        description=description,
        namespace=namespace,
        deferred=deferred,
        tags=tags,
        params=params,
        execution=execution,
        output=output,
    )


# ---------------------------------------------------------------------------
# Write capability through write-safety
# ---------------------------------------------------------------------------


class TestWriteCapability:
    """Monty tool with write capability goes through write-safety."""

    @pytest.mark.asyncio
    async def test_write_capability_goes_through_write_safety(
        self,
        tmp_path: Path,
    ) -> None:
        """A monty tool writing a file should use write-safety,
        observable via the stale tracker recording the write."""
        deps = _StubToolDeps(read_root=tmp_path)
        target_file = tmp_path / "output.txt"

        defn = _make_monty_definition(
            code="""
result = await write(path, content)
result
""",
            capabilities=["write"],
            params={
                "path": ParamDef(
                    type="string",
                    description="Target file path",
                    required=True,
                ),
                "content": ParamDef(
                    type="string",
                    description="File content",
                    required=True,
                ),
            },
        )
        tool = build_monty_tool(defn, deps)

        result = await tool.execute({
            "path": str(target_file),
            "content": "hello from monty",
        })

        # File was written.
        assert target_file.read_text() == "hello from monty"
        # Stale tracker recorded the write (observable write-safety):
        # after safe_write for a new file, record_read is called,
        # so the tracker's internal _reads dict should contain the path.
        canonical = target_file.resolve()
        assert canonical in deps.stale_tracker._reads  # noqa: SLF001
        assert deps.session_id in deps.stale_tracker._reads[canonical]  # noqa: SLF001
        # Result is returned.
        assert "Wrote" in result.text

    @pytest.mark.asyncio
    async def test_write_capability_carries_mutation_tag(
        self,
        tmp_path: Path,
    ) -> None:
        """A monty tool with write capability carries the mutation tag."""
        defn = _make_monty_definition(capabilities=["write"])
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert "mutation" in tool.tags
        assert "readonly" not in tool.tags


# ---------------------------------------------------------------------------
# Read capability
# ---------------------------------------------------------------------------


class TestReadCapability:
    """Monty tool with read capability works correctly."""

    @pytest.mark.asyncio
    async def test_read_capability_reads_file(
        self,
        tmp_path: Path,
    ) -> None:
        """A monty tool can read a file via the read capability."""
        test_file = tmp_path / "input.txt"
        test_file.write_text("file contents here")

        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code="""
content = await read(path)
content
""",
            capabilities=["read"],
            params={
                "path": ParamDef(
                    type="string",
                    description="File to read",
                    required=True,
                ),
            },
        )
        tool = build_monty_tool(defn, deps)
        result = await tool.execute({"path": str(test_file)})
        assert "file contents here" in result.text


# ---------------------------------------------------------------------------
# Command capability
# ---------------------------------------------------------------------------


class TestCommandCapability:
    """Monty tool with command capability runs subprocesses."""

    @pytest.mark.asyncio
    async def test_command_capability_runs_executable(
        self,
        tmp_path: Path,
    ) -> None:
        """Command capability runs the specified executable."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code="""
output = await command("echo", ["hello", "world"])
output
""",
            capabilities=["command"],
        )
        tool = build_monty_tool(defn, deps)
        result = await tool.execute({})
        # The result is the text output from the subprocess.
        assert "hello world" in result.text


# ---------------------------------------------------------------------------
# Infinite loop / resource limits
# ---------------------------------------------------------------------------


class TestResourceLimits:
    """Resource limits enforcement via MontyRuntimeError."""

    @pytest.mark.asyncio
    async def test_infinite_loop_dies_at_duration_limit(
        self,
        tmp_path: Path,
    ) -> None:
        """An infinite loop is terminated when max_duration_secs expires."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code="while True:\n    pass",
            max_duration_secs=1,
        )
        tool = build_monty_tool(defn, deps)
        with pytest.raises(ToolError, match="time limit exceeded"):
            await tool.execute({})


# ---------------------------------------------------------------------------
# Ungranted capability
# ---------------------------------------------------------------------------


class TestUngrantedCapability:
    """Ungranted capability hard-errors at script runtime."""

    @pytest.mark.asyncio
    async def test_calling_ungranted_capability_hard_errors(
        self,
        tmp_path: Path,
    ) -> None:
        """A script trying to call a function not in the capabilities
        dict gets a NameError (no such name in the sandbox)."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code="""
result = await write("test.txt", "data")
result
""",
            capabilities=[],  # No capabilities granted.
        )
        tool = build_monty_tool(defn, deps)
        with pytest.raises(ToolError, match="NameError"):
            await tool.execute({})


# ---------------------------------------------------------------------------
# Tag derivation
# ---------------------------------------------------------------------------


class TestTagDerivation:
    """Effect tags derived from capabilities."""

    def test_readonly_only_tool_carries_readonly_tag(self) -> None:
        """A tool with only read/http-GET capabilities carries readonly."""
        tags = _derive_tags(["read", "grep", "stat"], None)
        assert "readonly" in tags
        assert "mutation" not in tags

    def test_write_capability_adds_mutation_tag(self) -> None:
        """Any mutation capability adds the mutation tag."""
        tags = _derive_tags(["read", "write"], None)
        assert "mutation" in tags
        assert "readonly" not in tags

    def test_command_capability_adds_mutation_tag(self) -> None:
        """Command capability adds the mutation tag."""
        tags = _derive_tags(["command"], None)
        assert "mutation" in tags

    def test_user_tags_preserved(self) -> None:
        """User-supplied tags are preserved alongside derived tags."""
        tags = _derive_tags(["read"], ["custom_tag"])
        assert "custom_tag" in tags
        assert "readonly" in tags

    def test_empty_capabilities_gets_readonly(self) -> None:
        """No capabilities at all means readonly."""
        tags = _derive_tags([], None)
        assert "readonly" in tags

    def test_readonly_tag_on_built_tool(self, tmp_path: Path) -> None:
        """A monty tool built with readonly capabilities has readonly tag."""
        defn = _make_monty_definition(capabilities=["read", "stat"])
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert "readonly" in tool.tags
        assert "mutation" not in tool.tags

    def test_mutation_tag_on_built_tool(self, tmp_path: Path) -> None:
        """A monty tool built with write capability has mutation tag."""
        defn = _make_monty_definition(capabilities=["read", "write"])
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert "mutation" in tool.tags
        assert "readonly" not in tool.tags


# ---------------------------------------------------------------------------
# ToolError propagation from capabilities
# ---------------------------------------------------------------------------


class TestToolErrorPropagation:
    """ToolError from a capability propagates to the caller."""

    @pytest.mark.asyncio
    async def test_tool_error_from_read_propagates(
        self,
        tmp_path: Path,
    ) -> None:
        """A ToolError from a capability (e.g. file not found) propagates."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code="""
result = await read("/nonexistent/path/file.txt")
result
""",
            capabilities=["read"],
        )
        tool = build_monty_tool(defn, deps)
        with pytest.raises(ToolError):
            await tool.execute({})

    @pytest.mark.asyncio
    async def test_tool_error_caught_in_script(
        self,
        tmp_path: Path,
    ) -> None:
        """A ToolError from a capability can be caught inside the script."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code="""
try:
    await read("/nonexistent/path/file.txt")
    result = "unexpected success"
except Exception as e:
    result = f"caught: {type(e).__name__}"
result
""",
            capabilities=["read"],
        )
        tool = build_monty_tool(defn, deps)
        result = await tool.execute({})
        # The script caught the exception.
        assert "caught:" in result.text


# ---------------------------------------------------------------------------
# Output schema validation
# ---------------------------------------------------------------------------


class TestOutputSchemaValidation:
    """Output schema validated for monty tools."""

    @pytest.mark.asyncio
    async def test_matching_output_passes(
        self,
        tmp_path: Path,
    ) -> None:
        """Output matching the schema succeeds."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code='{"count": 42, "name": "test"}',
            output_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["count", "name"],
            },
        )
        tool = build_monty_tool(defn, deps)
        result = await tool.execute({})
        assert result.data["count"] == 42
        assert result.data["name"] == "test"

    @pytest.mark.asyncio
    async def test_missing_required_field_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """Output missing a required field raises ToolError."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code='{"count": 42}',
            output_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "name": {"type": "string"},
                },
                "required": ["count", "name"],
            },
        )
        tool = build_monty_tool(defn, deps)
        with pytest.raises(ToolError, match="Output validation failed"):
            await tool.execute({})

    @pytest.mark.asyncio
    async def test_wrong_type_fails(
        self,
        tmp_path: Path,
    ) -> None:
        """Output with wrong type raises ToolError."""
        deps = _StubToolDeps(read_root=tmp_path)
        defn = _make_monty_definition(
            code='{"count": "not_a_number"}',
            output_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                },
                "required": ["count"],
            },
        )
        tool = build_monty_tool(defn, deps)
        with pytest.raises(ToolError, match="Output validation failed"):
            await tool.execute({})


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


class TestToolMetadata:
    """Tool attributes match the definition."""

    def test_name_matches(self, tmp_path: Path) -> None:
        defn = _make_monty_definition(name="my_monty_tool")
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert tool.name == "my_monty_tool"

    def test_namespace_matches(self, tmp_path: Path) -> None:
        defn = _make_monty_definition(namespace="custom.myns")
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert tool.namespace == "custom.myns"

    def test_description_matches(self, tmp_path: Path) -> None:
        defn = _make_monty_definition(description="A special tool")
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert tool.description == "A special tool"

    def test_deferred_matches(self, tmp_path: Path) -> None:
        defn = _make_monty_definition(deferred=True)
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert tool.deferred is True

    def test_parameters_schema(self, tmp_path: Path) -> None:
        defn = _make_monty_definition(
            params={
                "query": ParamDef(
                    type="string",
                    description="Search query",
                    required=True,
                ),
            },
        )
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        assert "query" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["query"]


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


class TestArgValidation:
    """Monty tools validate args before execution."""

    @pytest.mark.asyncio
    async def test_unexpected_arg_raises_tool_error(
        self,
        tmp_path: Path,
    ) -> None:
        defn = _make_monty_definition(params={})
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        with pytest.raises(ToolError, match="Unexpected arguments"):
            await tool.execute({"unknown": "value"})

    @pytest.mark.asyncio
    async def test_missing_required_arg_raises_tool_error(
        self,
        tmp_path: Path,
    ) -> None:
        defn = _make_monty_definition(
            params={
                "name": ParamDef(
                    type="string",
                    description="Name",
                    required=True,
                ),
            },
        )
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        with pytest.raises(ToolError, match="Missing required argument"):
            await tool.execute({})


# ---------------------------------------------------------------------------
# Command execution type
# ---------------------------------------------------------------------------


class TestCommandTool:
    """Command execution type factory tests."""

    @pytest.mark.asyncio
    async def test_command_tool_runs_executable(
        self,
        tmp_path: Path,
    ) -> None:
        """Command tool runs the pinned executable."""
        defn = _make_command_definition(executable="echo")
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_command_tool(defn, deps)
        result = await tool.execute({"args": ["hello", "world"]})
        assert "hello world" in result.text

    def test_command_tool_carries_mutation_tag(
        self,
        tmp_path: Path,
    ) -> None:
        """Command tools always carry the mutation tag."""
        defn = _make_command_definition()
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_command_tool(defn, deps)
        assert "mutation" in tool.tags

    def test_wrong_execution_type_raises_type_error(
        self,
        tmp_path: Path,
    ) -> None:
        """build_command_tool rejects non-CommandExecution configs."""
        defn = _make_monty_definition()
        deps = _StubToolDeps(read_root=tmp_path)
        with pytest.raises(TypeError, match="Expected CommandExecution"):
            build_command_tool(defn, deps)

    def test_wrong_execution_type_for_monty_raises_type_error(
        self,
        tmp_path: Path,
    ) -> None:
        """build_monty_tool rejects non-MontyExecution configs."""
        defn = _make_command_definition()
        deps = _StubToolDeps(read_root=tmp_path)
        with pytest.raises(TypeError, match="Expected MontyExecution"):
            build_monty_tool(defn, deps)


# ---------------------------------------------------------------------------
# Unknown capability
# ---------------------------------------------------------------------------


class TestUnknownCapability:
    """Unknown capability names hard-error at construction time."""

    def test_unknown_capability_raises_value_error(
        self,
        tmp_path: Path,
    ) -> None:
        """An unrecognized capability name hard-errors."""
        defn = _make_monty_definition(
            capabilities=["nonexistent_capability"],
        )
        deps = _StubToolDeps(read_root=tmp_path)
        with pytest.raises(ValueError, match="Unknown capability"):
            build_monty_tool(defn, deps)


# ---------------------------------------------------------------------------
# Input passthrough
# ---------------------------------------------------------------------------


class TestInputPassthrough:
    """Monty tools pass agent arguments to the script as inputs."""

    @pytest.mark.asyncio
    async def test_args_available_as_inputs(
        self,
        tmp_path: Path,
    ) -> None:
        """Agent args are available as globals in the monty script."""
        defn = _make_monty_definition(
            code="f'{greeting}, {name}!'",
            params={
                "greeting": ParamDef(
                    type="string",
                    description="Greeting word",
                    required=True,
                ),
                "name": ParamDef(
                    type="string",
                    description="Name",
                    required=True,
                ),
            },
        )
        deps = _StubToolDeps(read_root=tmp_path)
        tool = build_monty_tool(defn, deps)
        result = await tool.execute({
            "greeting": "Hello",
            "name": "World",
        })
        assert result.text == "Hello, World!"
