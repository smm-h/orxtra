from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uuid6
from orxt.protocols._constraints import ConstraintKind
from orxt.scheduler._executor import Scheduler

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)


def _make_scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: uuid6.UUID,
    read_root: Path,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,
        transport_registry={
            "anthropic": transport,
        },
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=read_root,
    )


def _make_proc(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    """Create a mock process with the given exit code and output."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(
            stdout.encode(),
            stderr.encode(),
        ),
    )
    return proc


@pytest.fixture
def run_id() -> uuid6.UUID:
    return uuid6.uuid7()


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: uuid6.UUID,
    tmp_path: Path,
) -> Scheduler:
    return _make_scheduler(
        trace_writer, transport, run_id, tmp_path,
    )


@pytest.mark.asyncio
async def test_tests_pass_succeeds(
    scheduler: Scheduler,
) -> None:
    """tests_pass constraint passes when pytest exits 0."""
    task_id = uuid6.uuid7()
    mock_exec = AsyncMock(
        return_value=_make_proc(0, stdout="5 passed"),
    )
    with patch(
        "asyncio.create_subprocess_exec",
        mock_exec,
    ):
        result = await scheduler._check_constraint(  # noqa: SLF001
            ConstraintKind.TESTS_PASS, task_id,
        )
    assert result.passed is True
    assert "passed" in result.message.lower()


@pytest.mark.asyncio
async def test_tests_pass_fails(
    scheduler: Scheduler,
) -> None:
    """tests_pass constraint fails when pytest exits 1."""
    task_id = uuid6.uuid7()
    mock_exec = AsyncMock(
        return_value=_make_proc(
            1, stdout="FAILED test_foo.py::test_bar",
        ),
    )
    with patch(
        "asyncio.create_subprocess_exec",
        mock_exec,
    ):
        result = await scheduler._check_constraint(  # noqa: SLF001
            ConstraintKind.TESTS_PASS, task_id,
        )
    assert result.passed is False
    assert "failed" in result.message.lower()


@pytest.mark.asyncio
async def test_lint_clean_succeeds(
    scheduler: Scheduler,
) -> None:
    """lint_clean constraint passes when ruff exits 0."""
    task_id = uuid6.uuid7()
    mock_exec = AsyncMock(
        return_value=_make_proc(0, stdout=""),
    )
    with patch(
        "asyncio.create_subprocess_exec",
        mock_exec,
    ):
        result = await scheduler._check_constraint(  # noqa: SLF001
            ConstraintKind.LINT_CLEAN, task_id,
        )
    assert result.passed is True
    assert "clean" in result.message.lower()


@pytest.mark.asyncio
async def test_no_new_dependencies_unchanged(
    scheduler: Scheduler,
) -> None:
    """no_new_dependencies passes with empty git diff."""
    task_id = uuid6.uuid7()
    mock_exec = AsyncMock(
        return_value=_make_proc(0, stdout=""),
    )
    with patch(
        "asyncio.create_subprocess_exec",
        mock_exec,
    ):
        result = await scheduler._check_constraint(  # noqa: SLF001
            ConstraintKind.NO_NEW_DEPENDENCIES, task_id,
        )
    assert result.passed is True
    assert "no dependency" in result.message.lower()


@pytest.mark.asyncio
async def test_no_new_files_outside_inside_allowed(
    scheduler: Scheduler,
) -> None:
    """no_new_files_outside passes when new file is inside."""
    task_id = uuid6.uuid7()
    mock_exec = AsyncMock(
        return_value=_make_proc(
            0, stdout="?? src/new_module.py\n",
        ),
    )
    with patch(
        "asyncio.create_subprocess_exec",
        mock_exec,
    ):
        result = await scheduler._check_constraint(  # noqa: SLF001
            ConstraintKind.NO_NEW_FILES_OUTSIDE,
            task_id,
            constraint_text="no_new_files_outside(src/)",
        )
    assert result.passed is True
    assert "no new files outside" in result.message.lower()


@pytest.mark.asyncio
async def test_no_new_files_outside_outside_allowed(
    scheduler: Scheduler,
) -> None:
    """no_new_files_outside fails when new file is outside."""
    task_id = uuid6.uuid7()
    mock_exec = AsyncMock(
        return_value=_make_proc(
            0, stdout="?? docs/notes.txt\n",
        ),
    )
    with patch(
        "asyncio.create_subprocess_exec",
        mock_exec,
    ):
        result = await scheduler._check_constraint(  # noqa: SLF001
            ConstraintKind.NO_NEW_FILES_OUTSIDE,
            task_id,
            constraint_text="no_new_files_outside(src/)",
        )
    assert result.passed is False
    assert "docs/notes.txt" in result.message
