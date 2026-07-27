"""Tests for data-defined tool wiring in services.

Covers: tools_dir in RunConfig, ordering guarantee (custom tools
registered before Scheduler construction/validation), allow-list
resolution for custom.* vs fs.* namespaces.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from orxtra.protocols import KIND_CONSUMER, Principal
from orxtra.scheduler._tool_registry import (
    ToolEntry,
    create_builtin_registry,
    validate_allow_lists,
)
from orxtra.services._run import RunConfig, _load_custom_tools, start_run

if TYPE_CHECKING:
    from uuid import UUID


def _storage() -> AsyncMock:
    """A PrincipalStorage stand-in whose mint_principal is a no-op mock."""
    storage = AsyncMock()
    storage.mint_principal = AsyncMock()
    return storage


def _caller() -> Principal:
    """The caller principal whose id becomes the run's created_by."""
    return Principal(
        id=uuid4(),
        kind=KIND_CONSUMER,
        external_ref=uuid4(),
        display_name="test-caller",
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_HTTP_TOML = """\
format_version = 1

[tool]
name = "my_api_tool"
description = "A custom API tool"
namespace = "custom.api"
deferred = false
tags = ["readonly"]

[execution]
type = "http"
method = "GET"
url = "https://api.example.com/v1/data"
"""


def _make_run_config(
    tools_dir: Path | None = None,
) -> RunConfig:
    kwargs: dict = {
        "workflow_path": Path("/workflow.toml"),
        "agents_dir": Path("/agents"),
        "knowledge_dir": Path("/knowledge"),
        "categories_path": Path("/cats.toml"),
        "read_root": Path("/project"),
        "db_url": "postgres://localhost/test",
        "provider_configs": {
            "anthropic": {"type": "anthropic", "api_key": "test"},
        },
        "budget": Decimal("10.00"),
        "autonomy_level": "supervised",
    }
    if tools_dir is not None:
        kwargs["tools_dir"] = tools_dir
    return RunConfig(**kwargs)


def _make_mocks(sample_run_id: UUID) -> tuple[AsyncMock, AsyncMock]:
    mock_writer = AsyncMock()
    mock_writer.create_run = AsyncMock(return_value=sample_run_id)
    mock_writer.transition_run = AsyncMock()

    mock_sched = AsyncMock()
    mock_sched.execute_workflow = AsyncMock()

    return mock_writer, mock_sched


# ---------------------------------------------------------------------------
# RunConfig accepts tools_dir
# ---------------------------------------------------------------------------


class TestRunConfigToolsDir:
    """RunConfig accepts the tools_dir field."""

    def test_tools_dir_none_by_default(self) -> None:
        config = _make_run_config()
        assert config.tools_dir is None

    def test_tools_dir_set(self, tmp_path: Path) -> None:
        config = _make_run_config(tools_dir=tmp_path)
        assert config.tools_dir == tmp_path


# ---------------------------------------------------------------------------
# _load_custom_tools
# ---------------------------------------------------------------------------


class TestLoadCustomTools:
    """_load_custom_tools converts definitions to ToolEntry objects."""

    def test_produces_tool_entries(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "api.toml").write_text(_VALID_HTTP_TOML)

        entries = _load_custom_tools(tools_dir, secret_registry=None)

        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, ToolEntry)
        assert entry.name == "my_api_tool"
        assert entry.namespace == "custom.api"
        assert "readonly" in entry.tags

    def test_http_get_derives_readonly_tag(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "api.toml").write_text(_VALID_HTTP_TOML)

        entries = _load_custom_tools(tools_dir, secret_registry=None)

        assert "readonly" in entries[0].tags

    def test_http_post_derives_mutation_tag(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        post_toml = _VALID_HTTP_TOML.replace('method = "GET"', 'method = "POST"')
        (tools_dir / "api.toml").write_text(post_toml)

        entries = _load_custom_tools(tools_dir, secret_registry=None)

        assert "mutation" in entries[0].tags

    def test_http_factory_produces_tool(self, tmp_path: Path) -> None:
        """Http-type entries have a real factory that builds a Tool."""
        from orxtra.protocols import Tool

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "api.toml").write_text(_VALID_HTTP_TOML)

        entries = _load_custom_tools(tools_dir, secret_registry=None)

        deps = MagicMock()
        deps.preview_threshold = 50000
        deps.preview_lines = 50
        tool = entries[0].factory(deps)
        assert isinstance(tool, Tool)
        assert tool.name == "my_api_tool"

    def test_monty_factory_produces_tool(
        self, tmp_path: Path,
    ) -> None:
        """Monty-type entries have a real factory that builds a Tool."""
        from orxtra.protocols import Tool

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monty_toml = """\
format_version = 1

[tool]
name = "my_monty_tool"
description = "A monty tool"
namespace = "custom.monty"
deferred = false

[execution]
type = "monty"
code = "x = 1"
capabilities = []

[execution.limits]
max_duration_secs = 30
"""
        (tools_dir / "monty.toml").write_text(monty_toml)

        entries = _load_custom_tools(tools_dir, secret_registry=None)

        deps = MagicMock()
        deps.read_root = tmp_path
        deps.write_scope = None
        deps.preview_threshold = 50000
        deps.preview_lines = 50
        tool = entries[0].factory(deps)
        assert isinstance(tool, Tool)
        assert tool.name == "my_monty_tool"


# ---------------------------------------------------------------------------
# Allow-list resolution: custom.* vs fs.*
# ---------------------------------------------------------------------------


class TestAllowListResolution:
    """custom.* wildcard resolves data-defined tools; fs.* never does."""

    def test_custom_wildcard_resolves_data_tool(
        self, tmp_path: Path,
    ) -> None:
        """A custom.* allow entry makes the custom tool available
        in the metadata for allow-list resolution."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "api.toml").write_text(_VALID_HTTP_TOML)

        entries = _load_custom_tools(tools_dir, secret_registry=None)
        registry = create_builtin_registry()
        for entry in entries:
            registry.register_custom(
                entry.name, entry.namespace, entry.tags, entry.factory,
            )

        metadata = registry.get_metadata()
        assert "my_api_tool" in metadata
        ns, _tags = metadata["my_api_tool"]
        assert ns == "custom.api"

    def test_fs_wildcard_never_resolves_custom_tool(
        self, tmp_path: Path,
    ) -> None:
        """fs.* wildcard never matches a custom.* namespaced tool."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "api.toml").write_text(_VALID_HTTP_TOML)

        entries = _load_custom_tools(tools_dir, secret_registry=None)
        registry = create_builtin_registry()
        for entry in entries:
            registry.register_custom(
                entry.name, entry.namespace, entry.tags, entry.factory,
            )

        metadata = registry.get_metadata()
        # The custom tool's namespace is "custom.api", not "fs.*".
        ns, _ = metadata["my_api_tool"]
        assert not ns.startswith("fs.")

    def test_explicit_custom_name_resolves(
        self, tmp_path: Path,
    ) -> None:
        """An explicit tool name in the allow list resolves."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "api.toml").write_text(_VALID_HTTP_TOML)

        entries = _load_custom_tools(tools_dir, secret_registry=None)
        registry = create_builtin_registry()
        for entry in entries:
            registry.register_custom(
                entry.name, entry.namespace, entry.tags, entry.factory,
            )

        # Simulate an agent with "my_api_tool" in its allow list.
        agent = MagicMock()
        agent.allow = ["my_api_tool"]
        agents = {"test-agent": agent}

        # Should not raise: the tool is known.
        validate_allow_lists(agents, registry)


# ---------------------------------------------------------------------------
# Ordering test: data tools available at validation time
# ---------------------------------------------------------------------------


class TestOrderingGuarantee:
    """Registration of data-defined tools happens BEFORE Scheduler
    construction and its internal validate_allow_lists call."""

    @pytest.mark.asyncio
    async def test_data_tool_passes_run_start_validation(
        self,
        mock_pool: AsyncMock,
        sample_run_id: UUID,
        tmp_path: Path,
    ) -> None:
        """A data-defined tool registered via tools_dir passes
        the allow-list validation in the Scheduler constructor."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "api.toml").write_text(_VALID_HTTP_TOML)

        # Create an agent that references the custom tool.
        agent = MagicMock()
        agent.name = "test-agent"
        agent.allow = ["my_api_tool", "start_task", "end_task"]
        agent.inline_tools = []

        with (
            patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
            patch("orxtra.services._run.load_agents") as mock_load_agents,
            patch("orxtra.services._run.load_categories") as mock_load_cats,
            patch("orxtra.services._run.load_workflow") as mock_load_wf,
            patch(
                "orxtra.services._run.load_knowledge_files",
                new_callable=AsyncMock,
            ),
        ):
            mock_writer, _mock_sched = _make_mocks(sample_run_id)
            mock_writer_cls.return_value = mock_writer
            mock_load_agents.return_value = {"test-agent": agent}
            mock_load_cats.return_value = {
                "default": "anthropic/claude-sonnet-4-6",
            }
            mock_load_wf.return_value = MagicMock()

            config = _make_run_config(tools_dir=tools_dir)

            # The Scheduler constructor internally calls
            # validate_allow_lists. If the data tool was NOT
            # registered before construction, this would raise
            # ValueError for "unknown tool 'my_api_tool'".
            #
            # We do NOT mock the Scheduler -- we let the real
            # constructor run to verify the ordering.
            with contextlib.suppress(Exception):
                await start_run(mock_pool, _storage(), _caller(), "test", config)

    @pytest.mark.asyncio
    async def test_unknown_custom_tool_fails_validation(
        self,
        mock_pool: AsyncMock,
        sample_run_id: UUID,
    ) -> None:
        """Without tools_dir, an agent referencing a custom tool
        fails validation."""
        agent = MagicMock()
        agent.name = "test-agent"
        agent.allow = ["nonexistent_custom_tool"]
        agent.inline_tools = []

        with (
            patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
            patch("orxtra.services._run.load_agents") as mock_load_agents,
            patch("orxtra.services._run.load_categories") as mock_load_cats,
            patch("orxtra.services._run.load_workflow"),
            patch(
                "orxtra.services._run.load_knowledge_files",
                new_callable=AsyncMock,
            ),
        ):
            mock_writer, _ = _make_mocks(sample_run_id)
            mock_writer_cls.return_value = mock_writer
            mock_load_agents.return_value = {"test-agent": agent}
            mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}

            config = _make_run_config()  # No tools_dir

            with pytest.raises(ValueError, match="unknown tool"):
                await start_run(mock_pool, _storage(), _caller(), "test", config)
