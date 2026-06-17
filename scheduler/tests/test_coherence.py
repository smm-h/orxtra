"""Tests for coherence summary feature.

Verifies that the scheduler writes a coherence summary
from the Overseer session at run end, and skips
gracefully when no Overseer is configured.
"""

from __future__ import annotations

from pathlib import Path

import uuid6
from orxt.scheduler._executor import Scheduler
from orxt.transport import Result

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)


# -- Mock infrastructure ----------------------------------


class MockOverseerSession:
    """Mock session for coherence summary tests."""

    def __init__(
        self,
        response_text: str = "Mock coherence summary",
    ) -> None:
        self._response_text = response_text
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.messages_sent: list[str] = []
        self._model = "test-model"
        self._system_prompt = "test"
        self._tools: list[object] = []
        self._transport = None
        self._run_id = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def tools(self) -> list[object]:
        return self._tools

    def resume_id(self) -> str:
        return "mock-session-id"

    async def send(  # noqa: ANN201
        self, message: str,
    ):
        self.messages_sent.append(message)
        yield Result(
            text=self._response_text,
            session_id="mock-session",
            total_input_tokens=10,
            total_output_tokens=5,
        )


class MockOverseerAdapter:
    """Mock implementing OverseerInterface."""

    def __init__(self) -> None:
        self.events: list[object] = []
        self.corrections: list[str] = []
        self.verify_results: list[list[str]] = []
        self.degraded_types: set[str] = set()
        self.verify_call_count = 0
        self.mock_session: (
            MockOverseerSession | None
        ) = None

    async def send_event(
        self, event: object,
    ) -> None:
        self.events.append(event)

    async def verify_actions(
        self, event_type: str = "",
    ) -> list[str]:
        self.verify_call_count += 1
        if self.verify_results:
            return self.verify_results.pop(0)
        return []

    async def send_correction(
        self, message: str,
    ) -> None:
        self.corrections.append(message)

    def is_degraded(
        self, event_type: str,
    ) -> bool:
        return event_type in self.degraded_types

    @property
    def session(self) -> MockOverseerSession:
        if self.mock_session is None:
            self.mock_session = (
                MockOverseerSession()
            )
        return self.mock_session

    def update_session(
        self, new_session: object,
    ) -> None:
        self.mock_session = new_session  # type: ignore[assignment]


# -- Helpers ----------------------------------------------


def _make_scheduler(
    read_root: Path,
    trace_writer: MockTraceWriter | None = None,
    overseer: MockOverseerAdapter | None = None,
) -> Scheduler:
    tw = trace_writer or MockTraceWriter()
    transport = MockTransport()
    agents = {"test-agent": make_agent()}
    categories = make_categories()
    run_id = uuid6.uuid7()
    return Scheduler(
        trace_writer=tw,  # type: ignore[arg-type]
        transport_registry={
            "anthropic": transport,  # type: ignore[dict-item]
        },
        agents=agents,
        categories=categories,
        run_id=run_id,
        read_root=read_root,
        overseer_interface=overseer,  # type: ignore[arg-type]
    )


# -- Tests ------------------------------------------------


class TestCoherenceSummary:
    """Coherence summary at run end."""

    async def test_coherence_summary_written_at_run_end(
        self,
        tmp_path: Path,
    ) -> None:
        tw = MockTraceWriter()
        adapter = MockOverseerAdapter()
        adapter.mock_session = MockOverseerSession(
            response_text="test summary",
        )
        scheduler = _make_scheduler(
            read_root=tmp_path,
            trace_writer=tw,
            overseer=adapter,
        )

        await scheduler._write_coherence_summary()  # noqa: SLF001

        calls = tw.get_calls(
            "write_coherence_summary",
        )
        assert len(calls) == 1
        assert calls[0]["summary"] == "test summary"

    async def test_no_overseer_summary_skipped(
        self,
        tmp_path: Path,
    ) -> None:
        tw = MockTraceWriter()
        scheduler = _make_scheduler(
            read_root=tmp_path,
            trace_writer=tw,
            overseer=None,
        )

        await scheduler._write_coherence_summary()  # noqa: SLF001

        calls = tw.get_calls(
            "write_coherence_summary",
        )
        assert len(calls) == 0

    async def test_summary_text_comes_from_overseer_response(
        self,
        tmp_path: Path,
    ) -> None:
        tw = MockTraceWriter()
        adapter = MockOverseerAdapter()
        adapter.mock_session = MockOverseerSession(
            response_text="The run achieved X, Y, Z",
        )
        scheduler = _make_scheduler(
            read_root=tmp_path,
            trace_writer=tw,
            overseer=adapter,
        )

        await scheduler._write_coherence_summary()  # noqa: SLF001

        calls = tw.get_calls(
            "write_coherence_summary",
        )
        assert len(calls) == 1
        assert (
            calls[0]["summary"]
            == "The run achieved X, Y, Z"
        )
