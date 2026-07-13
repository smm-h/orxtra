from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import uuid6
from orxtra.agent import Agent
from orxtra.scheduler._executor import Scheduler

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable

# Cross-member test-infra seam: imports MockTraceWriter from the
# repo-root tests/shared_mocks.py. Uses absolute-path importlib
# loading (parents[2]) so it resolves correctly from both root and
# member cwd (--rootdir .). Do not change to a relative import.
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "tests.shared_mocks",
    Path(__file__).resolve().parents[2] / "tests" / "shared_mocks.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
MockTraceWriter = _mod.MockTraceWriter
MockTransport = _mod.MockTransport
TEST_RUN_PRINCIPAL_ID = _mod.TEST_RUN_PRINCIPAL_ID


def make_agent(
    name: str = "test-agent",
    category: str = "default",
) -> Agent:
    return Agent(
        name=name,
        description="A test agent",
        prompt="You are a test agent.",
        category=category,
        allow=["read"],
    )


def make_categories() -> dict[str, str]:
    return {"default": "anthropic/claude-sonnet-4-6"}


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport(auto_execute_tools=True)


@pytest.fixture
def agents() -> dict[str, Agent]:
    return {"test-agent": make_agent()}


@pytest.fixture
def categories() -> dict[str, str]:
    return make_categories()


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid6.uuid7()


@pytest.fixture
def scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents=agents,
        categories=categories,
        run_id=run_id,
        read_root=tmp_path,
        run_principal_id=TEST_RUN_PRINCIPAL_ID,
        autonomy_level="max",
    )


@pytest.fixture
def make_scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> Callable[..., Scheduler]:
    """Factory fixture for creating scheduler instances."""

    def _make(**kwargs: Any) -> Scheduler:
        defaults: dict[str, Any] = {
            "trace_writer": trace_writer,
            "transport_registry": {"anthropic": transport},
            "agents": agents,
            "categories": categories,
            "run_id": run_id,
            "read_root": tmp_path,
            "run_principal_id": TEST_RUN_PRINCIPAL_ID,
            "autonomy_level": "max",
        }
        defaults.update(kwargs)
        return Scheduler(**defaults)  # type: ignore[arg-type]

    return _make
