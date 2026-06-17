"""Tests for session state reload via create_session."""

from __future__ import annotations

import uuid
from typing import Any

from orxt.session._factory import create_session

from .conftest import MockTraceWriter, MockTransport


class MockPool:
    """Mock asyncpg pool that returns canned transcript data."""

    def __init__(
        self,
        fetch_rows: list[dict[str, Any]] | None = None,
        fetchval_result: int | None = None,
    ) -> None:
        self._fetch_rows = fetch_rows or []
        self._fetchval_result = fetchval_result

    async def fetch(
        self, query: str, *args: object,
    ) -> list[dict[str, Any]]:
        return self._fetch_rows

    async def fetchval(
        self, query: str, *args: object,
    ) -> int | None:
        return self._fetchval_result


class TestSessionReload:
    async def test_reload_restores_tokens(
        self,
    ) -> None:
        pool = MockPool(
            fetch_rows=[
                {
                    "tokens": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "reasoning_tokens": 10,
                        "cache_read_tokens": 5,
                        "cache_write_tokens": 3,
                    },
                },
                {
                    "tokens": {
                        "input_tokens": 200,
                        "output_tokens": 80,
                        "reasoning_tokens": 20,
                        "cache_read_tokens": 15,
                        "cache_write_tokens": 7,
                    },
                },
            ],
            fetchval_result=4,
        )
        transport = MockTransport()
        trace_writer = MockTraceWriter()
        run_id = uuid.uuid4()
        session_id = str(uuid.uuid4())

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=session_id,
            pool=pool,
        )

        assert session.total_input_tokens == 300
        assert session.total_output_tokens == 130
        assert session.total_reasoning_tokens == 30
        assert session.total_cache_read_tokens == 20
        assert session.total_cache_write_tokens == 10

    async def test_reload_restores_turn_count(
        self,
    ) -> None:
        pool = MockPool(
            fetch_rows=[],
            fetchval_result=7,
        )
        transport = MockTransport()
        trace_writer = MockTraceWriter()
        run_id = uuid.uuid4()
        session_id = str(uuid.uuid4())

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=session_id,
            pool=pool,
        )

        assert session.turn_count == 7

    async def test_reload_no_pool_skips(
        self,
    ) -> None:
        transport = MockTransport()
        trace_writer = MockTraceWriter()
        run_id = uuid.uuid4()
        session_id = str(uuid.uuid4())

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=session_id,
        )

        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.total_reasoning_tokens == 0
        assert session.total_cache_read_tokens == 0
        assert session.total_cache_write_tokens == 0
        assert session.turn_count == 0

    async def test_reload_no_session_id_skips(
        self,
    ) -> None:
        pool = MockPool(
            fetch_rows=[
                {"tokens": {"input_tokens": 100, "output_tokens": 50}},
            ],
            fetchval_result=3,
        )
        transport = MockTransport()
        trace_writer = MockTraceWriter()
        run_id = uuid.uuid4()

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            pool=pool,
        )

        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.turn_count == 0
