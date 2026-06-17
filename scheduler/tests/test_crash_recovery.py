from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from orxt.protocols._task import TaskSpec
from orxt.scheduler._executor import Scheduler
from orxt.scheduler._types import WorkflowConfig
from orxt.trace import RunLockError

if TYPE_CHECKING:
    import uuid
    from pathlib import Path

    from orxt.agent import Agent

    from tests.conftest import (
        MockTraceWriter,
        MockTransport,
    )


def _simple_config() -> WorkflowConfig:
    return WorkflowConfig(
        name="test",
        description="test workflow",
        tasks=[
            TaskSpec(
                name="t1",
                agent="test-agent",
                task_prompt="do it",
                timeout=60,
                context_refinement=False,
            ),
        ],
        dependencies={},
    )


def _make_scheduler(  # noqa: PLR0913
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    read_root: Path,
    *,
    pool: object | None = None,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents=agents,
        categories=categories,
        run_id=run_id,
        read_root=read_root,
        pool=pool,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_recovery_called_at_startup(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> None:
    """All three recovery functions are called when pool is provided."""
    mock_pool = AsyncMock()
    sched = _make_scheduler(
        trace_writer, transport, agents, categories,
        run_id, tmp_path, pool=mock_pool,
    )

    with (
        patch(
            "orxt.trace.reclaim_interrupted",
            new_callable=AsyncMock,
            return_value=0,
        ) as mock_reclaim,
        patch(
            "orxt.trace.reevaluate_blocked",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_reeval,
        patch(
            "orxt.trace.clean_orphaned",
            new_callable=AsyncMock,
            return_value=0,
        ) as mock_clean,
        patch(
            "orxt.trace.acquire_run_lock",
            new_callable=AsyncMock,
        ) as mock_lock,
    ):
        await sched.execute_workflow(_simple_config())

        mock_reclaim.assert_called_once_with(mock_pool)
        mock_reeval.assert_called_once_with(mock_pool)
        mock_clean.assert_called_once_with(mock_pool)
        mock_lock.assert_called_once_with(
            mock_pool, run_id,
        )


@pytest.mark.asyncio
async def test_advisory_lock_acquired(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> None:
    """acquire_run_lock is called with the correct pool and run_id."""
    mock_pool = AsyncMock()
    sched = _make_scheduler(
        trace_writer, transport, agents, categories,
        run_id, tmp_path, pool=mock_pool,
    )

    with (
        patch(
            "orxt.trace.reclaim_interrupted",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "orxt.trace.reevaluate_blocked",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "orxt.trace.clean_orphaned",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "orxt.trace.acquire_run_lock",
            new_callable=AsyncMock,
        ) as mock_lock,
    ):
        await sched.execute_workflow(_simple_config())

        mock_lock.assert_called_once_with(
            mock_pool, run_id,
        )


@pytest.mark.asyncio
async def test_recovery_skipped_without_pool(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> None:
    """Recovery functions are NOT called when pool is None."""
    sched = _make_scheduler(
        trace_writer, transport, agents, categories,
        run_id, tmp_path,
    )

    with (
        patch(
            "orxt.trace.reclaim_interrupted",
            new_callable=AsyncMock,
        ) as mock_reclaim,
        patch(
            "orxt.trace.reevaluate_blocked",
            new_callable=AsyncMock,
        ) as mock_reeval,
        patch(
            "orxt.trace.clean_orphaned",
            new_callable=AsyncMock,
        ) as mock_clean,
        patch(
            "orxt.trace.acquire_run_lock",
            new_callable=AsyncMock,
        ) as mock_lock,
    ):
        await sched.execute_workflow(_simple_config())

        mock_reclaim.assert_not_called()
        mock_reeval.assert_not_called()
        mock_clean.assert_not_called()
        mock_lock.assert_not_called()


@pytest.mark.asyncio
async def test_lock_error_propagates(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> None:
    """RunLockError from acquire_run_lock propagates to the caller."""
    mock_pool = AsyncMock()
    sched = _make_scheduler(
        trace_writer, transport, agents, categories,
        run_id, tmp_path, pool=mock_pool,
    )

    with (
        patch(
            "orxt.trace.reclaim_interrupted",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "orxt.trace.reevaluate_blocked",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "orxt.trace.clean_orphaned",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "orxt.trace.acquire_run_lock",
            new_callable=AsyncMock,
            side_effect=RunLockError(
                f"run {run_id} is already locked",
            ),
        ),pytest.raises(RunLockError)
    ):
        await sched.execute_workflow(
            _simple_config(),
        )


@pytest.mark.asyncio
async def test_recovery_order(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> None:
    """Recovery functions are called in the correct order:
    reclaim, reevaluate, clean, then lock."""
    mock_pool = AsyncMock()
    sched = _make_scheduler(
        trace_writer, transport, agents, categories,
        run_id, tmp_path, pool=mock_pool,
    )

    call_order: list[str] = []

    async def track_reclaim(pool: object) -> int:
        call_order.append("reclaim_interrupted")
        return 0

    async def track_reeval(pool: object) -> list[object]:
        call_order.append("reevaluate_blocked")
        return []

    async def track_clean(pool: object) -> int:
        call_order.append("clean_orphaned")
        return 0

    async def track_lock(
        pool: object, rid: uuid.UUID,
    ) -> None:
        call_order.append("acquire_run_lock")

    with (
        patch(
            "orxt.trace.reclaim_interrupted",
            side_effect=track_reclaim,
        ),
        patch(
            "orxt.trace.reevaluate_blocked",
            side_effect=track_reeval,
        ),
        patch(
            "orxt.trace.clean_orphaned",
            side_effect=track_clean,
        ),
        patch(
            "orxt.trace.acquire_run_lock",
            side_effect=track_lock,
        ),
    ):
        await sched.execute_workflow(_simple_config())

    assert call_order == [
        "reclaim_interrupted",
        "reevaluate_blocked",
        "clean_orphaned",
        "acquire_run_lock",
    ]


@pytest.mark.asyncio
async def test_recovery_before_knowledge_loading(  # noqa: PLR0913
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> None:
    """Recovery runs before knowledge loading when both
    pool and knowledge_dir are set."""
    mock_pool = AsyncMock()

    call_order: list[str] = []

    async def track_knowledge(
        *args: object, **kwargs: object,
    ) -> None:
        call_order.append("load_knowledge_files")

    sched = Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents=agents,
        categories=categories,
        run_id=run_id,
        read_root=tmp_path,
        pool=mock_pool,  # type: ignore[arg-type]
        knowledge_dir=tmp_path,  # type: ignore[arg-type]
        knowledge_loader=track_knowledge,  # type: ignore[arg-type]
    )

    async def track_reclaim(pool: object) -> int:
        call_order.append("reclaim_interrupted")
        return 0

    async def track_reeval(
        pool: object,
    ) -> list[object]:
        call_order.append("reevaluate_blocked")
        return []

    async def track_clean(pool: object) -> int:
        call_order.append("clean_orphaned")
        return 0

    async def track_lock(
        pool: object, rid: uuid.UUID,
    ) -> None:
        call_order.append("acquire_run_lock")

    with (
        patch(
            "orxt.trace.reclaim_interrupted",
            side_effect=track_reclaim,
        ),
        patch(
            "orxt.trace.reevaluate_blocked",
            side_effect=track_reeval,
        ),
        patch(
            "orxt.trace.clean_orphaned",
            side_effect=track_clean,
        ),
        patch(
            "orxt.trace.acquire_run_lock",
            side_effect=track_lock,
        ),
    ):
        await sched.execute_workflow(
            _simple_config(),
        )

    # Recovery must come before knowledge loading
    assert call_order.index(
        "reclaim_interrupted",
    ) < call_order.index("load_knowledge_files")
    assert call_order.index(
        "acquire_run_lock",
    ) < call_order.index("load_knowledge_files")
