from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from orxtra.protocols import KIND_CONSUMER, Principal
from orxtra.services._run import (
    RunConfig,
    _redact_db_url,
    _serialize_config,
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

    from orxtra.trace import RunReport, RunSummary


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


@pytest.fixture(autouse=True)
def _pin_run_id(sample_run_id: UUID) -> Iterator[None]:
    """Pin the run id start_run generates so assertions stay deterministic.

    start_run now mints its own run_id (uuid7) before the row exists; patching
    the generator to sample_run_id keeps the existing per-test assertions
    (result, scheduler run_id, transition targets) valid.
    """
    with patch("orxtra.services._run.uuid6.uuid7", return_value=sample_run_id):
        yield


@pytest.mark.asyncio
async def test_start_run_creates_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
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
            read_root=Path("/project"),
            db_url="postgres://localhost/test",
            provider_configs={"anthropic": {"type": "anthropic", "api_key": "test"}},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
        )
        result = await start_run(mock_pool, _storage(), _caller(), "test intent", config)

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
        'format_version = 1\n'
        'workflow_path = "/workflow.toml"\n'
        'agents_dir = "/agents"\n'
        'knowledge_dir = "/knowledge"\n'
        'categories_path = "/cats.toml"\n'
        'read_root = "/project"\n'
        'db_url = "postgres://localhost/test"\n'
        'budget = "10.00"\n'
        'autonomy_level = "supervised"\n'
        "\n"
        "[provider_configs.anthropic]\n"
        'type = "anthropic"\n'
        'api_key = "test"\n'
    )
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
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

        result = await start_run_from_file(mock_pool, _storage(), None, None, _caller(), "test", config_file)

        assert result == sample_run_id
        mock_writer.create_run.assert_called_once()


@pytest.mark.asyncio
async def test_start_run_from_file_missing(mock_pool: AsyncMock) -> None:
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        await start_run_from_file(mock_pool, _storage(), None, None, _caller(), "test", Path("/nonexistent.toml"))


@pytest.mark.asyncio
async def test_get_run_delegates(
    mock_pool: AsyncMock, sample_run_report: RunReport
) -> None:
    with patch(
        "orxtra.services._run.read_run_report", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = sample_run_report

        result = await get_run(mock_pool, sample_run_report.id)

        assert result == sample_run_report
        mock_read.assert_called_once_with(mock_pool, sample_run_report.id)


@pytest.mark.asyncio
async def test_get_run_not_found(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch(
        "orxtra.services._run.read_run_report", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = None

        result = await get_run(mock_pool, sample_run_id)

        assert result is None


@pytest.mark.asyncio
async def test_list_runs_delegates(
    mock_pool: AsyncMock, sample_run_summary: RunSummary
) -> None:
    with patch("orxtra.services._run._list_runs", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [sample_run_summary]

        result = await list_runs(mock_pool)

        assert result == [sample_run_summary]
        mock_list.assert_called_once_with(mock_pool)


_RUN_PRINCIPAL_ID = uuid4()


@pytest.mark.asyncio
async def test_abort_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch("orxtra.services._run.TraceWriter") as mock_writer_cls, patch(
        "orxtra.services._run._resolve_run_principal_id",
        AsyncMock(return_value=_RUN_PRINCIPAL_ID),
    ):
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        await abort_run(mock_pool, sample_run_id)

        mock_writer.transition_run.assert_called_once_with(
            sample_run_id, "aborted", principal_id=_RUN_PRINCIPAL_ID,
        )


@pytest.mark.asyncio
async def test_pause_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch("orxtra.services._run.TraceWriter") as mock_writer_cls, patch(
        "orxtra.services._run._resolve_run_principal_id",
        AsyncMock(return_value=_RUN_PRINCIPAL_ID),
    ):
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        await pause_run(mock_pool, sample_run_id)

        mock_writer.transition_run.assert_called_once_with(
            sample_run_id, "paused", principal_id=_RUN_PRINCIPAL_ID,
        )


@pytest.mark.asyncio
async def test_resume_run(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    with patch("orxtra.services._run.TraceWriter") as mock_writer_cls, patch(
        "orxtra.services._run._resolve_run_principal_id",
        AsyncMock(return_value=_RUN_PRINCIPAL_ID),
    ):
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        await resume_run(mock_pool, sample_run_id)

        mock_writer.transition_run.assert_called_once_with(
            sample_run_id, "running", principal_id=_RUN_PRINCIPAL_ID,
        )


def test_run_config_valid() -> None:
    config = RunConfig(
        workflow_path=Path("/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        read_root=Path("/project"),
        db_url="postgres://localhost/test",
        provider_configs={"anthropic": {"type": "anthropic", "api_key": "key"}},
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
            read_root=Path("/project"),
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
            read_root=Path("/project"),
            db_url="postgres://localhost/test",
            provider_configs={},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
        )


# Helper comment: all new tests below use the same 5-mock pattern


def _make_mocks(sample_run_id: UUID) -> tuple:
    """Set up the standard 5-mock context for start_run tests.

    Returns (mock_writer, mock_sched).
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
        read_root=Path("/project"),
        db_url="postgres://localhost/test",
        provider_configs={"anthropic": {"type": "anthropic", "api_key": "test"}},
        budget=Decimal("10.00"),
        autonomy_level="supervised",
    )


@pytest.mark.asyncio
async def test_start_run_constructs_scheduler(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        result = await start_run(mock_pool, _storage(), _caller(), "test intent", _default_config())

        assert result == sample_run_id
        mock_scheduler_cls.assert_called_once()
        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["trace_writer"] is mock_writer
        assert call_kwargs["run_id"] == sample_run_id
        assert "knowledge_dir" not in call_kwargs


@pytest.mark.asyncio
async def test_start_run_with_transport_registry(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        custom_registry = {"anthropic": MagicMock()}
        await start_run(
            mock_pool, _storage(), _caller(), "test", _default_config(), transport_registry=custom_registry,
        )

        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["transport_registry"] is custom_registry


@pytest.mark.asyncio
async def test_start_run_loads_agents(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        agents = {"test-agent": MagicMock()}
        mock_load_agents.return_value = agents
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, _storage(), _caller(), "test", _default_config())

        mock_load_agents.assert_called_once_with(Path("/agents"))
        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["agents"] is agents


@pytest.mark.asyncio
async def test_start_run_loads_categories(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        categories = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_cats.return_value = categories
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, _storage(), _caller(), "test", _default_config())

        mock_load_cats.assert_called_once_with(Path("/cats.toml"))
        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["categories"] is categories


@pytest.mark.asyncio
async def test_start_run_loads_workflow(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        wf_config = MagicMock()
        mock_load_wf.return_value = wf_config
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, _storage(), _caller(), "test", _default_config())

        mock_load_wf.assert_called_once_with(Path("/workflow.toml"))
        mock_sched.execute_workflow.assert_called_once_with(wf_config)


@pytest.mark.asyncio
async def test_start_run_transitions_to_running(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, _storage(), _caller(), "test", _default_config())

        # First transition_run call should be "running", attributed to the
        # run's own principal.
        calls = mock_writer.transition_run.call_args_list
        assert len(calls) >= 1
        assert calls[0].args == (sample_run_id, "running")
        assert "principal_id" in calls[0].kwargs


@pytest.mark.asyncio
async def test_start_run_transitions_to_completed(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, _storage(), _caller(), "test", _default_config())

        # Second transition_run call should be "completed"
        calls = mock_writer.transition_run.call_args_list
        assert len(calls) == 2
        assert calls[1].args == (sample_run_id, "completed")
        assert "principal_id" in calls[1].kwargs


@pytest.mark.asyncio
async def test_start_run_transitions_to_failed_on_error(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_sched.execute_workflow = AsyncMock(side_effect=RuntimeError("boom"))
        mock_scheduler_cls.return_value = mock_sched

        with pytest.raises(RuntimeError, match="boom"):
            await start_run(mock_pool, _storage(), _caller(), "test", _default_config())

        # Should transition to "running" then "failed"
        calls = mock_writer.transition_run.call_args_list
        assert len(calls) == 2
        assert calls[0].args == (sample_run_id, "running")
        assert calls[1].args == (sample_run_id, "failed")
        assert "principal_id" in calls[0].kwargs
        assert "principal_id" in calls[1].kwargs


@pytest.mark.asyncio
async def test_start_run_from_file_with_workflow_path(
    mock_pool: AsyncMock, sample_run_id: UUID, tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'format_version = 1\n'
        'workflow_path = "/my/workflow.toml"\n'
        'agents_dir = "/agents"\n'
        'knowledge_dir = "/knowledge"\n'
        'categories_path = "/cats.toml"\n'
        'read_root = "/project"\n'
        'db_url = "postgres://localhost/test"\n'
        'budget = "10.00"\n'
        'autonomy_level = "supervised"\n'
        "\n"
        "[provider_configs.anthropic]\n"
        'type = "anthropic"\n'
        'api_key = "test"\n'
    )
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run_from_file(mock_pool, _storage(), None, None, _caller(), "test", config_file)

        # Verify the workflow_path was parsed and used
        mock_load_wf.assert_called_once_with(Path("/my/workflow.toml"))


def test_serialize_config_redacts_credentials() -> None:
    """Regression: raw credentials must never reach the persisted run record.

    _serialize_config's output is stored verbatim in the runs table via
    create_run, so api_keys and db_url passwords must be redacted.
    """
    sentinel_key = "sk-ant-SENTINEL-API-KEY-12345"
    sentinel_password = "SENTINEL-DB-PASSWORD"
    config = RunConfig(
        workflow_path=Path("/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        read_root=Path("/project"),
        db_url=f"postgres://orxtra:{sentinel_password}@localhost:5432/test",
        provider_configs={
            "anthropic": {"type": "anthropic", "api_key": sentinel_key},
            "openai": {"type": "openai", "api_key": sentinel_key},
        },
        budget=Decimal("10.00"),
        autonomy_level="supervised",
    )

    serialized = json.dumps(_serialize_config(config))

    assert sentinel_key not in serialized
    assert sentinel_password not in serialized
    # Redaction, not omission: the snapshot shape stays honest.
    assert "[REDACTED]" in serialized
    data = json.loads(serialized)
    assert data["provider_configs"]["anthropic"]["api_key"] == "[REDACTED]"
    assert data["provider_configs"]["anthropic"]["type"] == "anthropic"
    assert data["provider_configs"]["openai"]["api_key"] == "[REDACTED]"
    assert data["db_url"] == "postgres://orxtra:[REDACTED]@localhost:5432/test"


def test_serialize_config_db_url_without_password_unchanged() -> None:
    config = _default_config()
    data = _serialize_config(config)
    assert data["db_url"] == "postgres://localhost/test"


def test_serialize_config_redacts_query_param_password() -> None:
    """Regression: asyncpg/libpq accept ?password=... in the URL query."""
    sentinel_password = "SENTINEL-QUERY-PASSWORD"
    config = RunConfig(
        workflow_path=Path("/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        read_root=Path("/project"),
        db_url=(
            "postgres://localhost:5432/test"
            f"?sslmode=require&password={sentinel_password}&application_name=orxtra"
        ),
        provider_configs={},
        budget=Decimal("10.00"),
        autonomy_level="supervised",
    )

    serialized = json.dumps(_serialize_config(config))

    assert sentinel_password not in serialized
    data = json.loads(serialized)
    # Other query params and URL structure preserved verbatim.
    assert data["db_url"] == (
        "postgres://localhost:5432/test"
        "?sslmode=require&password=[REDACTED]&application_name=orxtra"
    )


def test_redact_db_url_ipv6_host_preserves_brackets() -> None:
    """Regression: the hostname property strips IPv6 brackets on rebuild."""
    result = _redact_db_url("postgres://user:hunter2@[::1]:5432/db")
    assert result == "postgres://user:[REDACTED]@[::1]:5432/db"


def test_redact_db_url_netloc_preserved_verbatim_except_password() -> None:
    """Regression: rebuilding netloc from parsed properties mangles it.

    The percent-encoded username and the host (including its case, which
    the hostname property lowercases) must survive verbatim; only the
    password is replaced.
    """
    result = _redact_db_url(
        "postgres://user%40corp%3Ax:hunter2@DB.Example.com:5432/db"
    )
    assert result == "postgres://user%40corp%3Ax:[REDACTED]@DB.Example.com:5432/db"


def test_run_config_accepts_secrets_env() -> None:
    config = RunConfig(
        workflow_path=Path("/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        read_root=Path("/project"),
        db_url="postgres://localhost/test",
        provider_configs={},
        budget=Decimal("5.00"),
        autonomy_level="autonomous",
        secrets_env={"API_KEY": "MY_API_KEY_ENV"},
    )
    assert config.secrets_env == {"API_KEY": "MY_API_KEY_ENV"}


def test_run_config_secrets_env_defaults_to_none() -> None:
    config = _default_config()
    assert config.secrets_env is None


@pytest.mark.asyncio
async def test_start_run_with_secrets_env_passes_registry(
    mock_pool: AsyncMock, sample_run_id: UUID, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When secrets_env is set, start_run constructs a SecretRegistry
    via the factory and passes it to the Scheduler."""
    monkeypatch.setenv("TEST_TOKEN_VAR", "real-token-value")
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        config = RunConfig(
            workflow_path=Path("/workflow.toml"),
            agents_dir=Path("/agents"),
            knowledge_dir=Path("/knowledge"),
            categories_path=Path("/cats.toml"),
            read_root=Path("/project"),
            db_url="postgres://localhost/test",
            provider_configs={"anthropic": {"type": "anthropic", "api_key": "test"}},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
            secrets_env={"TOKEN": "TEST_TOKEN_VAR"},
        )
        await start_run(mock_pool, _storage(), _caller(), "test intent", config)

        mock_scheduler_cls.assert_called_once()
        call_kwargs = mock_scheduler_cls.call_args[1]
        secret_reg = call_kwargs["secret_registry"]
        # The factory read the env var and the Scheduler got a real registry
        assert secret_reg is not None
        assert secret_reg.resolve("TOKEN") == "real-token-value"


@pytest.mark.asyncio
async def test_start_run_without_secrets_env_passes_none(
    mock_pool: AsyncMock, sample_run_id: UUID,
) -> None:
    """When secrets_env is not set, start_run passes None for secret_registry
    (backward-compatible behavior)."""
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
    ):
        mock_writer, mock_sched = _make_mocks(sample_run_id)
        mock_writer_cls.return_value = mock_writer
        mock_load_agents.return_value = {"test-agent": MagicMock()}
        mock_load_cats.return_value = {"default": "anthropic/claude-sonnet-4-6"}
        mock_load_wf.return_value = MagicMock()
        mock_scheduler_cls.return_value = mock_sched

        await start_run(mock_pool, _storage(), _caller(), "test", _default_config())

        call_kwargs = mock_scheduler_cls.call_args[1]
        assert call_kwargs["secret_registry"] is None


def test_run_config_with_workflow_path() -> None:
    config = RunConfig(
        workflow_path=Path("/my/workflow.toml"),
        agents_dir=Path("/agents"),
        knowledge_dir=Path("/knowledge"),
        categories_path=Path("/cats.toml"),
        read_root=Path("/project"),
        db_url="postgres://localhost/test",
        provider_configs={"anthropic": {"type": "anthropic", "api_key": "key"}},
        budget=Decimal("5.00"),
        autonomy_level="autonomous",
    )
    assert config.workflow_path == Path("/my/workflow.toml")


@pytest.mark.asyncio
async def test_start_run_calls_sweep(mock_pool: AsyncMock, sample_run_id: UUID) -> None:
    """start_run invokes sweep_orphaned_run_principals after create_run."""
    with (
        patch("orxtra.services._run.TraceWriter") as mock_writer_cls,
        patch("orxtra.services._run.load_agents") as mock_load_agents,
        patch("orxtra.services._run.load_categories") as mock_load_cats,
        patch("orxtra.services._run.load_workflow") as mock_load_wf,
        patch("orxtra.services._run.Scheduler") as mock_scheduler_cls,
        patch(
            "orxtra.services._identity.sweep_orphaned_run_principals",
        ) as mock_sweep,
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

        storage = _storage()
        config = RunConfig(
            workflow_path=Path("/workflow.toml"),
            agents_dir=Path("/agents"),
            knowledge_dir=Path("/knowledge"),
            categories_path=Path("/cats.toml"),
            read_root=Path("/project"),
            db_url="postgres://localhost/test",
            provider_configs={"anthropic": {"type": "anthropic", "api_key": "test"}},
            budget=Decimal("10.00"),
            autonomy_level="supervised",
        )
        await start_run(mock_pool, storage, _caller(), "test intent", config)

        mock_sweep.assert_called_once_with(storage)
