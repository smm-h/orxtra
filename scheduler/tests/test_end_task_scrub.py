"""Tests for secret scrubbing in the end_task output path.

Verifies that when an agent calls end_task with a message containing
a real secret value (after pipeline substitution), the stored task
output contains the placeholder, not the real value.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import uuid6
from orxtra.protocols import TaskSpec, TaskState
from orxtra.scheduler._executor import Scheduler
from orxtra.secrets import SecretRegistry

from .conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)


def _simple_task(
    name: str = "t1",
    agent: str = "test-agent",
    timeout: int = 60,
) -> TaskSpec:
    return TaskSpec(
        name=name,
        agent=agent,
        task_prompt="do something",
        timeout=timeout,
    )


def _make_scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: uuid.UUID,
    read_root: Any,
    secret_registry: SecretRegistry | None = None,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=read_root,
        autonomy_level="max",
        secret_registry=secret_registry,
    )


class TestEndTaskOutputScrubbing:
    """Secrets in end_task messages must be scrubbed before storage."""

    @pytest.mark.asyncio
    async def test_secret_in_end_task_message_scrubbed_from_task_outputs(
        self,
        trace_writer: MockTraceWriter,
        transport: MockTransport,
        run_id: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """A secret value in the end_task message must appear as a placeholder
        in task outputs, not as the real value."""
        registry = SecretRegistry({"DB_PASS": "hunter2-secret"})
        scheduler = _make_scheduler(
            trace_writer, transport, run_id, tmp_path,
            secret_registry=registry,
        )

        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001
        scheduler._task_children[task_id] = []  # noqa: SLF001
        scheduler._task_parents[task_id] = None  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(task_id))

        # Agent calls end_task with a message containing a real secret
        # (which got there via pipeline secret substitution in tool args).
        await scheduler.handle_end_task(
            "sess-1",
            "Completed. DB password is hunter2-secret.",
        )

        # The stored output must contain the placeholder, not the secret.
        outputs = scheduler._task_outputs.get(None, {})  # noqa: SLF001
        stored = outputs.get("t1", "")
        assert "hunter2-secret" not in (stored or ""), (
            "Real secret value leaked into task outputs"
        )
        assert "{{secret:DB_PASS}}" in (stored or ""), (
            "Placeholder not found in scrubbed task output"
        )

    @pytest.mark.asyncio
    async def test_no_registry_end_task_message_unchanged(
        self,
        trace_writer: MockTraceWriter,
        transport: MockTransport,
        run_id: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """Without a secret registry, end_task message passes through unchanged."""
        scheduler = _make_scheduler(
            trace_writer, transport, run_id, tmp_path,
            secret_registry=None,
        )

        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001
        scheduler._task_children[task_id] = []  # noqa: SLF001
        scheduler._task_parents[task_id] = None  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(task_id))
        await scheduler.handle_end_task("sess-1", "normal message")

        outputs = scheduler._task_outputs.get(None, {})  # noqa: SLF001
        assert outputs["t1"] == "normal message"

    @pytest.mark.asyncio
    async def test_pending_end_task_message_also_scrubbed(
        self,
        trace_writer: MockTraceWriter,
        transport: MockTransport,
        run_id: uuid.UUID,
        tmp_path: Any,
    ) -> None:
        """The _pending_end_task_message dict also stores the scrubbed value."""
        registry = SecretRegistry({"TOKEN": "tok-99"})
        scheduler = _make_scheduler(
            trace_writer, transport, run_id, tmp_path,
            secret_registry=registry,
        )

        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001
        scheduler._task_children[task_id] = []  # noqa: SLF001
        scheduler._task_parents[task_id] = None  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(task_id))
        await scheduler.handle_end_task(
            "sess-1", "result: tok-99",
        )

        # The pending message (used for auto-commit) must be scrubbed too.
        pending = scheduler._pending_end_task_message  # noqa: SLF001
        assert "tok-99" not in str(pending)
