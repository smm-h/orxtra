from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxt.services._run import (
    RunConfig,
    abort_run,
    get_run,
    list_runs,
    pause_run,
    resume_run,
    start_run,
    start_run_from_file,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from uuid import UUID

    from orxt.trace import RunReport, RunSummary


@pytest.mark.asyncio
async def test_start_run_creates_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer = AsyncMock()
        mock_writer.create_run = AsyncMock(return_value=sample_run_id)
        mock_writer.transition_run = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()

        mock_sched = AsyncMock()
        mock_sched.execute_workflow = AsyncMock()
        mock_scheduler_cls.return_value = mock_sched

        config = RunConfig(
            workflow_path=Path("/workflow.toml"),
            agents_dir=Path("/agents"),
            knowledge_dir=Path("/knowledge"),
            categories_path=Path("/cats.toml"),
            db_url="postgres://localhost/test",
            provider_configs={"anthropic": {"api_key": "test"}},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
        )
        result = await start_run(mock_pool, "test intent", config)

        assert result == sample_run_id
        mock_writer.create_run.assert_called_once()
        call_args = mock_writer.create_run.call_args
        assert call_args[0][0] == "test intent"
        assert call_args[0][2] == "supervised"


@pytest.mark.asyncio
async def test_start_run_from_file(
    mock_pool: AsyncMock, sample_run_id: UUID, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'workflow_path = "/workflow.toml"\n'
        'agents_dir = "/agents"\n'
        'knowledge_dir = "/knowledge"\n'
        'categories_path = "/cats.toml"\n'
        'db_url = "postgres://localhost/test"\n'
        'budget = "10.00"\n'
        'autonomy_level = "supervised"\n'
        "\n"
        "[provider_configs.anthropic]\n"
        'api_key = "test"\n'
    )
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer = AsyncMock()
        mock_writer.create_run = AsyncMock(return_value=sample_run_id)
        mock_writer.transition_run = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()

        mock_sched = AsyncMock()
        mock_sched.execute_workflow = AsyncMock()
        mock_scheduler_cls.return_value = mock_sched

        result = await start_run_from_file(mock_pool, "test", config_file)

        assert result == sample_run_id
        mock_writer.create_run.assert_called_once()


@pytest.mark.asyncio
async def test_start_run_from_file_missing(mock_pool: AsyncMock) -> None:
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        await start_run_from_file(mock_pool, "test", Path("/nonexistent.toml"))


@pytest.mark.asyncio
async def test_get_run_delegates(
    mock_pool: AsyncMock, sample_run_report: RunReport
) -> None:
    with patch(
        "orxt.services._run.read_run_report", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = sample_run_report

        result = await get_run(mock_pool, sample_run_report.id)

        assert result == sample_run_report
        mock_read.assert_called_once_with(mock_pool, sample_run_report.id)


@pytest.mark.asyncio
async def test_get_run_not_found(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch(
        "orxt.services._run.read_run_report", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = None

        result = await get_run(mock_pool, sample_run_id)

        assert result is None


@pytest.mark.asyncio
async def test_list_runs_delegates(
    mock_pool: AsyncMock, sample_run_summary: RunSummary
) -> None:
    with patch("orxt.services._run._list_runs", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [sample_run_summary]

        result = await list_runs(mock_pool)

        assert result == [sample_run_summary]
        mock_list.assert_called_once_with(mock_pool)


@pytest.mark.asyncio
async def test_abort_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch("orxt.services._run.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        await abort_run(mock_pool, sample_run_id)

        mock_writer.transition_run.assert_called_once_with(sample_run_id, "aborted")


@pytest.mark.asyncio
async def test_pause_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch("orxt.services._run.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        await pause_run(mock_pool, sample_run_id)

        mock_writer.transition_run.assert_called_once_with(sample_run_id, "paused")


@pytest.mark.asyncio
async def test_resume_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch("orxt.services._run.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        await resume_run(mock_pool, sample_run_id)

        mock_writer.transition_run.assert_called_once_with(sample_run_id, "running")


def test_run_config_valid() -> None:
    config = RunConfig(
        workflow_path=Path("/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        db_url="postgres://localhost/test",
        provider_configs={"anthropic": {"api_key": "key"}},
        budget=Decimal("5.00"),
        autonomy_level="autonomous",
    )
    assert config.autonomy_level == "autonomous"
    assert config.budget == Decimal("5.00")


def test_run_config_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        RunConfig(
            workflow_path=Path("/workflow.toml"),
            agents_dir=Path("/agents"),
            knowledge_dir=Path("/knowledge"),
            categories_path=Path("/cats.toml"),
            db_url="postgres://localhost/test",
            provider_configs={},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
            unexpected_field="boom",
        )


def test_run_config_missing_field() -> None:
    with pytest.raises(ValidationError):
        RunConfig(
            workflow_path=Path("/workflow.toml"),
            agents_dir=Path("/agents"),
            knowledge_dir=Path("/knowledge"),
            # categories_path missing
            db_url="postgres://localhost/test",
            provider_configs={},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
        )


# Helper comment: all new tests below use the same 5-mock pattern


def _make_mocks(sample_run_id: UUID) -> tuple:
    """Set up the standard 5-mock context for start_run tests.

    Returns (mock_writer, mock_load_agents, mock_load_cats, mock_load_wf, mock_scheduler_cls, mock_sched).
    The caller must use patch() to wire these in.
    """
    mock_writer = AsyncMock()
    mock_writer.create_run = AsyncMock(return_value=sample_run_id)
    mock_writer.transition_run = AsyncMock()

    mock_sched = AsyncMock()
    mock_sched.execute_workflow = AsyncMock()

    return mock_writer, mock_sched


def _default_config() -> RunConfig:
    return RunConfig(
        workflow_path=Path("/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        db_url="postgres://localhost/test",
        provider_configs={"anthropic": {"api_key": "test"}},
        budget=Decimal("10.00"),
        autonomy_level="supervised",
    )


@pytest.mark.asyncio
async def test_start_run_constructs_scheduler(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        result = await start_run(mock_pool, "test intent", _default_config())

        assert result == sample_run_id
        mock_scheduler_cls.assert_called_once()
        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["trace_writer"] is mock_writer
        assert call_kwargs["run_id"] == sample_run_id
        assert call_kwargs["knowledge_dir"] == Path("/knowledge")


@pytest.mark.asyncio
async def test_start_run_with_transport_registry(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        custom_registry = {"anthropic": MagicMock()}
        await start_run(
            mock_pool, "test", _default_config(), transport_registry=custom_registry,
        )

        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["transport_registry"] is custom_registry


@pytest.mark.asyncio
async def test_start_run_loads_agents(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        agents = {"test-agent": MagicMock()}
        mock_load_agents.return_value = agents
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, "test", _default_config())

        mock_load_agents.assert_called_once_with(Path("/agents"))
        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["agents"] is agents


@pytest.mark.asyncio
async def test_start_run_loads_categories(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        categories = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_cats.return_value = categories
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, "test", _default_config())

        mock_load_cats.assert_called_once_with(Path("/cats.toml"))
        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["categories"] is categories


@pytest.mark.asyncio
async def test_start_run_loads_workflow(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        wf_config = MagicMock()
        mock_load_wf.return_value = wf_config
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, "test", _default_config())

        mock_load_wf.assert_called_once_with(Path("/workflow.toml"))
        mock_sched.execute_workflow.assert_called_once_with(wf_config)


@pytest.mark.asyncio
async def test_start_run_transitions_to_running(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, "test", _default_config())

        # First transition_run call should be "running"
        calls = mock_writer.transition_run.call_args_list
        assert len(calls) >= 1
        assert calls[0] == ((sample_run_id, "running"),)


@pytest.mark.asyncio
async def test_start_run_transitions_to_completed(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, "test", _default_config())

        # Second transition_run call should be "completed"
        calls = mock_writer.transition_run.call_args_list
        assert len(calls) == 2
        assert calls[1] == ((sample_run_id, "completed"),)


@pytest.mark.asyncio
async def test_start_run_transitions_to_failed_on_error(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_sched.execute_workflow = AsyncMock(side_effect=RuntimeError("boom"))
        mock_scheduler_cls.return_value = mock_sched

        with pytest.raises(RuntimeError, match="boom"):
            await start_run(mock_pool, "test", _default_config())

        # Should transition to "running" then "failed"
        calls = mock_writer.transition_run.call_args_list
        assert len(calls) == 2
        assert calls[0] == ((sample_run_id, "running"),)
        assert calls[1] == ((sample_run_id, "failed"),)


@pytest.mark.asyncio
async def test_start_run_from_file_with_workflow_path(
    mock_pool: AsyncMock, sample_run_id: UUID, tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'workflow_path = "/my/workflow.toml"\n'
        'agents_dir = "/agents"\n'
        'knowledge_dir = "/knowledge"\n'
        'categories_path = "/cats.toml"\n'
        'db_url = "postgres://localhost/test"\n'
        'budget = "10.00"\n'
        'autonomy_level = "supervised"\n'
        "\n"
        "[provider_configs.anthropic]\n"
        'api_key = "test"\n'
    )
    with (
        patch("orxt.services._run.TraceWriter") as mock_writer_cls,
        patch("orxt.services._run.load_agents") as mock_load_agents,
        patch("orxt.services._run.load_categories") as mock_load_cats,
        patch("orxt.services._run.load_workflow") as mock_load_wf,
        patch("orxt.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run_from_file(mock_pool, "test", config_file)

        # Verify the workflow_path was parsed and used
        mock_load_wf.assert_called_once_with(Path("/my/workflow.toml"))


def test_run_config_with_workflow_path() -> None:
    config = RunConfig(
        workflow_path=Path("/my/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        db_url="postgres://localhost/test",
        provider_configs={"anthropic": {"api_key": "key"}},
        budget=Decimal("5.00"),
        autonomy_level="autonomous",
    )
    assert config.workflow_path == Path("/my/workflow.toml")
