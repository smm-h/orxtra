"""Drift-sentinel test: both pipelines use the shared scrub module.

Asserts that the local pipeline (tool/_pipeline.py) and the remote
pipeline (worker/_pipeline_split.py) both import from tool._scrub,
and that both produce identical scrubbed output for the same input.

This test catches divergence if someone modifies one pipeline's
scrubbing without updating the other. The shared extraction into a
single core module is planned for Phase 3.3; until then this sentinel
guards against drift.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from orxtra.protocols import Tool, ToolOutput
from orxtra.secrets import SecretRegistry
from orxtra.tool._pipeline import wrap_tool_with_pipeline
from orxtra.tool._scrub import scrub_data, scrub_text, scrub_tool_output
from orxtra.worker._pipeline_split import wrap_tool_for_remote
from orxtra.worker._protocol import ToolCallResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = "session-drift-test"
_TASK_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_SECRET_VALUE = "drift-sentinel-secret-xyz-789"
_SECRET_NAME = "DRIFT_KEY"


def _passing_scheduler_check(session_id: str) -> UUID:
    return _TASK_ID


# ---------------------------------------------------------------------------
# Drift sentinel: import source
# ---------------------------------------------------------------------------


class TestPipelineImportDrift:
    """Both pipelines must import scrub functions from tool._scrub."""

    def test_local_pipeline_imports_scrub_tool_output(self) -> None:
        """tool/_pipeline.py imports scrub_tool_output from tool._scrub."""
        import orxtra.tool._pipeline as mod  # noqa: PLC0415

        source = inspect.getsource(mod)
        assert "from orxtra.tool._scrub import" in source
        assert "scrub_tool_output" in source

    def test_remote_pipeline_imports_scrub_functions(self) -> None:
        """worker/_pipeline_split.py imports scrub_text/scrub_data from tool._scrub."""
        import orxtra.worker._pipeline_split as mod  # noqa: PLC0415

        source = inspect.getsource(mod)
        assert "from orxtra.tool._scrub import" in source
        assert "scrub_text" in source
        assert "scrub_data" in source

    def test_lifecycle_handlers_import_scrub_text(self) -> None:
        """scheduler/_lifecycle_handlers.py imports scrub_text from tool._scrub."""
        import orxtra.scheduler._lifecycle_handlers as mod  # noqa: PLC0415

        source = inspect.getsource(mod)
        assert "from orxtra.tool._scrub import" in source
        assert "scrub_text" in source


# ---------------------------------------------------------------------------
# Drift sentinel: output equivalence
# ---------------------------------------------------------------------------


class TestPipelineOutputEquivalence:
    """Both pipelines produce identical scrubbed output for the same input."""

    @pytest.mark.asyncio
    async def test_local_and_remote_scrub_same_text(self) -> None:
        """Local and remote pipelines scrub result text identically."""
        registry = SecretRegistry({_SECRET_NAME: _SECRET_VALUE})

        # Local pipeline tool
        async def _local_exec(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(
                data=f"data-{_SECRET_VALUE}",
                text=f"text-{_SECRET_VALUE}",
            )

        local_tool = Tool(
            name="test", description="t",
            parameters={"type": "object"}, execute=_local_exec,
        )
        wrapped_local = wrap_tool_with_pipeline(
            tool=local_tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        local_result = await wrapped_local.execute({})

        # Remote pipeline tool -- mock the worker to return the same raw output
        async def _send_to_worker(call: Any) -> ToolCallResult:
            return ToolCallResult(
                call_id=call.call_id,
                output=f"text-{_SECRET_VALUE}",
                data=f"data-{_SECRET_VALUE}",
                mutations=[],
                error=None,
            )

        remote_tool = Tool(
            name="test", description="t",
            parameters={"type": "object"}, execute=_local_exec,
        )
        wrapped_remote = wrap_tool_for_remote(
            tool=remote_tool,
            send_to_worker_fn=_send_to_worker,
            secret_registry=registry,
            scheduler_check=_passing_scheduler_check,
            trace_callback=None,
            mutation_tracker=None,
            session_id=_SESSION_ID,
        )
        remote_result = await wrapped_remote.execute({})

        # Both must scrub identically
        assert local_result.text == remote_result.text
        assert _SECRET_VALUE not in local_result.text
        assert _SECRET_VALUE not in remote_result.text
        assert f"{{{{secret:{_SECRET_NAME}}}}}" in local_result.text
        assert f"{{{{secret:{_SECRET_NAME}}}}}" in remote_result.text

    @pytest.mark.asyncio
    async def test_local_and_remote_scrub_same_data(self) -> None:
        """Local and remote pipelines scrub result data identically."""
        registry = SecretRegistry({_SECRET_NAME: _SECRET_VALUE})

        data_with_secret = {"header": f"Bearer {_SECRET_VALUE}"}

        # Local pipeline
        async def _local_exec(args: dict[str, Any]) -> ToolOutput[dict[str, str]]:
            return ToolOutput(
                data=dict(data_with_secret),
                text="ok",
            )

        local_tool = Tool(
            name="test", description="t",
            parameters={"type": "object"}, execute=_local_exec,
        )
        wrapped_local = wrap_tool_with_pipeline(
            tool=local_tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        local_result = await wrapped_local.execute({})

        # Remote pipeline
        async def _send_to_worker(call: Any) -> ToolCallResult:
            return ToolCallResult(
                call_id=call.call_id,
                output="ok",
                data=dict(data_with_secret),
                mutations=[],
                error=None,
            )

        remote_tool = Tool(
            name="test", description="t",
            parameters={"type": "object"}, execute=_local_exec,
        )
        wrapped_remote = wrap_tool_for_remote(
            tool=remote_tool,
            send_to_worker_fn=_send_to_worker,
            secret_registry=registry,
            scheduler_check=_passing_scheduler_check,
            trace_callback=None,
            mutation_tracker=None,
            session_id=_SESSION_ID,
        )
        remote_result = await wrapped_remote.execute({})

        # Both must produce the same scrubbed data
        assert local_result.data == remote_result.data
        assert _SECRET_VALUE not in str(local_result.data)
        assert _SECRET_VALUE not in str(remote_result.data)
