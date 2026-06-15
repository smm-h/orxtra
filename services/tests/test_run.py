from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

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
    with patch("orxt.services._run.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer.create_run = AsyncMock(return_value=sample_run_id)
        mock_writer_cls.return_value = mock_writer

        config = RunConfig(
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
    with patch("orxt.services._run.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer.create_run = AsyncMock(return_value=sample_run_id)
        mock_writer_cls.return_value = mock_writer

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
            agents_dir=Path("/agents"),
            knowledge_dir=Path("/knowledge"),
            # categories_path missing
            db_url="postgres://localhost/test",
            provider_configs={},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
        )
