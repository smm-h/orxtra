"""End-to-end failure path integration tests for the orxtra scheduler.

Tests cover: postcheck failure with retry, retry exhaustion leading to
escalation, timeout cancellation, missing start_task, missing end_task,
error classification, and exception-during-session retry.
"""
from __future__ import annotations

import asyncio
import errno
import json
import re
from typing import TYPE_CHECKING, Any

from orxtra.protocols import CheckResult, ErrorCategory, TaskState
from orxtra.scheduler import Scheduler, classify_error
from orxtra.transport import Result, StepFinish, ToolUse

from tests.conftest import (
    AgentTurn,
    IntegrationMockTransport,
    MockTraceWriter,
    make_scheduler,
    simple_task,
    simple_workflow,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator

    from orxtra.protocols import Tool
    from orxtra.transport import Event


def _extract_task_id(message: str) -> str | None:
    """Extract the task_id from the scheduler's prompt prefix."""
    match = re.search(r"Your task ID is ([0-9a-f-]+)", message)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Test 1: Postcheck failure with retry
# ---------------------------------------------------------------------------


class TestPostcheckFailureWithRetry:
    async def test_postcheck_fails_then_passes_on_retry(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Postcheck fails on first attempt, agent retries, passes on second.

        Verifies: two task attempts created, task ends in COMPLETED state.
        """
        trace_writer = MockTraceWriter()
        postcheck_count = 0

        async def fail_then_pass(
            self_sched: Scheduler,
            task: Any,  # noqa: ANN401
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            nonlocal postcheck_count
            postcheck_count += 1
            if postcheck_count <= 1:
                return [
                    CheckResult(
                        passed=False,
                        message="First attempt postcheck fail",
                    ),
                ]
            return [
                CheckResult(
                    passed=True,
                    message="Second attempt postcheck pass",
                ),
            ]

        transport = IntegrationMockTransport([
            AgentTurn(
                tool_calls=[
                    ("start_task", {}),
                    ("end_task", {"message": "attempt 1"}),
                ],
            ),
            AgentTurn(
                tool_calls=[
                    ("start_task", {}),
                    ("end_task", {"message": "attempt 2"}),
                ],
            ),
        ])

        task = simple_task(
            retry=1,
            retry_resume=False,
            retry_inject_failure=True,
        )
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(trace_writer, transport, run_id)

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = fail_then_pass  # type: ignore[assignment]  # noqa: SLF001
        try:
            await scheduler.execute_workflow(config)
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001

        # Two attempts were created
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        assert len(attempt_calls) == 2
        assert attempt_calls[0]["attempt"] == 1
        assert attempt_calls[1]["attempt"] == 2

        # Task reached COMPLETED
        completed = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.COMPLETED
        ]
        assert len(completed) == 1

        # Both turns consumed
        assert transport.turns_consumed == 2


# ---------------------------------------------------------------------------
# Test 2: Retry exhaustion -> escalation
# ---------------------------------------------------------------------------


class TestRetryExhaustionEscalation:
    async def test_all_retries_fail_produces_escalation(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """All retry attempts fail postchecks. Task enters ESCALATED state.

        Verifies: 3 attempts created (retry=2 -> 3 total), task ESCALATED,
        result has escalation structured output.
        """
        trace_writer = MockTraceWriter()

        async def always_fail(
            self_sched: Scheduler,
            task: Any,  # noqa: ANN401
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            return [
                CheckResult(
                    passed=False,
                    message="Postcheck always fails",
                ),
            ]

        transport = IntegrationMockTransport([
            AgentTurn(
                tool_calls=[
                    ("start_task", {}),
                    ("end_task", {"message": "attempt 1"}),
                ],
            ),
            AgentTurn(
                tool_calls=[
                    ("start_task", {}),
                    ("end_task", {"message": "attempt 2"}),
                ],
            ),
            AgentTurn(
                tool_calls=[
                    ("start_task", {}),
                    ("end_task", {"message": "attempt 3"}),
                ],
            ),
        ])

        task = simple_task(
            retry=2,
            retry_resume=False,
            retry_inject_failure=True,
        )
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(trace_writer, transport, run_id)

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = always_fail  # type: ignore[assignment]  # noqa: SLF001
        try:
            await scheduler.execute_workflow(config)
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001

        # Three attempts were created
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        assert len(attempt_calls) == 3

        # Task is in ESCALATED state
        escalated = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.ESCALATED
        ]
        assert len(escalated) == 1

        # Transition trace includes escalated
        transitions = trace_writer.get_calls("transition_task")
        escalated_transitions = [
            t for t in transitions
            if t["new_status"] == "escalated"
        ]
        assert len(escalated_transitions) >= 1

        # All three turns consumed
        assert transport.turns_consumed == 3


# ---------------------------------------------------------------------------
# Test 3: Timeout cancels task
# ---------------------------------------------------------------------------


class TestTimeoutCancelsTask:
    async def test_slow_session_times_out_and_cancels(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Agent session exceeds timeout. Task enters CANCELLED state.

        Uses a custom slow transport that sleeps beyond the timeout window
        after calling start_task.
        """
        trace_writer = MockTraceWriter()

        class SlowTransport:
            """Transport that calls start_task then sleeps indefinitely."""

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
            ) -> AsyncIterator[Event]:
                _ = model, system_prompt
                import uuid6  # noqa: PLC0415

                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                task_id_str = _extract_task_id(message)
                start_args = {"task_id": task_id_str} if task_id_str else {}

                # Call start_task to transition to ACTIVE
                if "start_task" in tool_map:
                    result = await tool_map["start_task"].execute(start_args)
                    yield ToolUse(
                        tool_name="start_task",
                        input=start_args,
                        output=result,
                        status="success",
                    )

                # Sleep well beyond the timeout
                await asyncio.sleep(30)

                # These should never be reached due to cancellation
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="",
                    session_id=sid,
                    total_input_tokens=0,
                    total_output_tokens=0,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        # Use a very short timeout (1 second)
        task = simple_task(timeout=1)
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(
            trace_writer, SlowTransport(), run_id,  # type: ignore[arg-type]
        )

        await scheduler.execute_workflow(config)

        # Task should be CANCELLED
        cancelled = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.CANCELLED
        ]
        assert len(cancelled) == 1

        # fail_task_attempt should have been called with timeout error
        fail_calls = trace_writer.get_calls("fail_task_attempt")
        assert len(fail_calls) == 1
        assert "timed out" in fail_calls[0]["error"].lower()


# ---------------------------------------------------------------------------
# Test 4: Agent never calls start_task
# ---------------------------------------------------------------------------


class TestMissingStartTask:
    async def test_agent_skips_start_task_records_error(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Agent returns text without calling start_task.

        Task stays in CREATED state. The scheduler's attempt loop falls
        through to the 'else' branch and records an error in prior_attempts.
        With no retries, task escalates.
        """
        trace_writer = MockTraceWriter()

        # Transport that does not call any tools
        transport = IntegrationMockTransport([
            AgentTurn(
                tool_calls=[],
                text_response="I did nothing",
            ),
        ])

        task = simple_task()
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(trace_writer, transport, run_id)

        await scheduler.execute_workflow(config)

        # Task should be ESCALATED (no retries, fell through to escalation)
        escalated = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.ESCALATED
        ]
        assert len(escalated) == 1

        # One attempt was created
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        assert len(attempt_calls) == 1

        # The attempt was completed with a fail verdict
        complete_calls = trace_writer.get_calls(
            "complete_task_attempt",
        )
        assert len(complete_calls) == 1
        assert complete_calls[0]["check_verdict"] == "fail"


# ---------------------------------------------------------------------------
# Test 5: Agent never calls end_task
# ---------------------------------------------------------------------------


class TestMissingEndTask:
    async def test_agent_calls_start_but_not_end(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Agent calls start_task but never end_task. Session ends with
        task in ACTIVE state.

        The scheduler's attempt loop detects the non-terminal state and
        records an error. With no retries, task escalates.
        """
        trace_writer = MockTraceWriter()

        transport = IntegrationMockTransport([
            AgentTurn(
                tool_calls=[("start_task", {})],
                text_response="Started but forgot to end",
            ),
        ])

        task = simple_task()
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(trace_writer, transport, run_id)

        await scheduler.execute_workflow(config)

        # Task should be ESCALATED
        escalated = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.ESCALATED
        ]
        assert len(escalated) == 1

        # One attempt created
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        assert len(attempt_calls) == 1

        # Transition trace shows the task went to ACTIVE (via start_task)
        # but never to COMPLETED or POSTCHECKING
        transitions = trace_writer.get_calls("transition_task")
        active_transitions = [
            t for t in transitions
            if t["new_status"] == "active"
        ]
        assert len(active_transitions) >= 1

        completed_transitions = [
            t for t in transitions
            if t["new_status"] == "completed"
        ]
        assert len(completed_transitions) == 0


# ---------------------------------------------------------------------------
# Test 6: Error classification
# ---------------------------------------------------------------------------


class TestErrorClassification:
    async def test_timeout_error_is_infra(self) -> None:
        assert classify_error(TimeoutError()) == ErrorCategory.INFRA

    async def test_asyncio_timeout_error_is_infra(self) -> None:
        assert classify_error(TimeoutError()) == ErrorCategory.INFRA

    async def test_json_decode_error_is_parse(self) -> None:
        err = json.JSONDecodeError("bad json", "", 0)
        assert classify_error(err) == ErrorCategory.PARSE

    async def test_import_error_is_build_env(self) -> None:
        assert classify_error(ImportError()) == ErrorCategory.BUILD_ENV

    async def test_module_not_found_is_build_env(self) -> None:
        assert classify_error(ModuleNotFoundError()) == ErrorCategory.BUILD_ENV

    async def test_assertion_error_is_logic(self) -> None:
        assert classify_error(AssertionError()) == ErrorCategory.LOGIC

    async def test_runtime_error_is_unclassified(self) -> None:
        assert classify_error(RuntimeError()) == ErrorCategory.UNCLASSIFIED

    async def test_oserror_connrefused_is_infra(self) -> None:
        err = OSError(errno.ECONNREFUSED, "Connection refused")
        assert classify_error(err) == ErrorCategory.INFRA

    async def test_oserror_connreset_is_infra(self) -> None:
        err = OSError(errno.ECONNRESET, "Connection reset")
        assert classify_error(err) == ErrorCategory.INFRA

    async def test_oserror_non_network_is_unclassified(self) -> None:
        err = OSError(errno.ENOENT, "No such file")
        assert classify_error(err) == ErrorCategory.UNCLASSIFIED

    async def test_value_error_is_unclassified(self) -> None:
        assert classify_error(ValueError("bad")) == ErrorCategory.UNCLASSIFIED


# ---------------------------------------------------------------------------
# Test 7: Exception during session classified and retried
# ---------------------------------------------------------------------------


class TestExceptionDuringSession:
    async def test_exception_triggers_classification_and_retry(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """An exception during the session triggers error classification.
        With retries available, the scheduler retries. On the second attempt,
        the agent succeeds.

        Verifies: task_error event written with category, two attempts,
        task completes on second attempt.
        """
        trace_writer = MockTraceWriter()
        send_count = 0

        class FailOnceThenSucceedTransport:
            """First send() raises RuntimeError. Second send() succeeds."""

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
            ) -> AsyncIterator[Event]:
                _ = model, system_prompt
                nonlocal send_count
                send_count += 1
                import uuid6  # noqa: PLC0415

                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                task_id_str = _extract_task_id(message)
                start_args = {"task_id": task_id_str} if task_id_str else {}

                if send_count == 1:
                    # Call start_task, then raise
                    if "start_task" in tool_map:
                        result = await tool_map["start_task"].execute(start_args)
                        yield ToolUse(
                            tool_name="start_task",
                            input=start_args,
                            output=result,
                            status="success",
                        )
                    msg = "Simulated transport failure"
                    raise RuntimeError(msg)

                # Second attempt: succeed normally
                if "start_task" in tool_map:
                    result = await tool_map["start_task"].execute(start_args)
                    yield ToolUse(
                        tool_name="start_task",
                        input=start_args,
                        output=result,
                        status="success",
                    )
                if "end_task" in tool_map:
                    result = await tool_map["end_task"].execute(
                        {"message": "recovered"},
                    )
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "recovered"},
                        output=result,
                        status="success",
                    )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="recovered",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        task = simple_task(
            retry=1,
            retry_resume=False,
            retry_inject_failure=True,
        )
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(
            trace_writer,
            FailOnceThenSucceedTransport(),  # type: ignore[arg-type]
            run_id,
        )

        await scheduler.execute_workflow(config)

        # Two attempts were created
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        assert len(attempt_calls) == 2

        # A task_error event was written for the first attempt
        events = trace_writer.get_calls("write_event")
        error_events = [
            e for e in events
            if e["event_type"] == "task_error"
        ]
        assert len(error_events) == 1
        assert error_events[0]["data"]["category"] == "unclassified"
        assert "Simulated transport failure" in error_events[0]["data"]["error"]

        # Task completed on second attempt
        completed = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.COMPLETED
        ]
        assert len(completed) == 1

        assert send_count == 2


# ---------------------------------------------------------------------------
# Test 8: Missing start_task with retries
# ---------------------------------------------------------------------------


class TestMissingStartTaskWithRetry:
    async def test_missing_start_task_retried_then_succeeds(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Agent fails to call start_task on first attempt but succeeds
        on second attempt.

        Verifies: first attempt records error for wrong state, second
        attempt completes.
        """
        trace_writer = MockTraceWriter()

        transport = IntegrationMockTransport([
            # First attempt: no tool calls
            AgentTurn(
                tool_calls=[],
                text_response="Oops, forgot start_task",
            ),
            # Second attempt: proper behavior
            AgentTurn(
                tool_calls=[
                    ("start_task", {}),
                    ("end_task", {"message": "done properly"}),
                ],
            ),
        ])

        task = simple_task(
            retry=1,
            retry_resume=False,
            retry_inject_failure=True,
        )
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(trace_writer, transport, run_id)

        await scheduler.execute_workflow(config)

        # Two attempts created
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        assert len(attempt_calls) == 2

        # Task completed on second attempt
        completed = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.COMPLETED
        ]
        assert len(completed) == 1

        # Both turns consumed
        assert transport.turns_consumed == 2
