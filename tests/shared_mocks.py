from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import uuid6
from orxtra.protocols._tool import Tool, ToolError
from orxtra.transport import Continuation, Event, Result, StepFinish, ToolUse

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator
    from decimal import Decimal


class MockTraceWriter:
    """Records all calls for verification."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._run_statuses: dict[uuid.UUID, str] = {}
        self._task_statuses: dict[uuid.UUID, str] = {}
        self._event_callback: Any = None
        self._control_callback: Any = None

    def _record(
        self, method: str, **kwargs: object,
    ) -> None:
        self.calls.append((method, dict(kwargs)))

    async def create_run(
        self,
        intent: str,
        config: dict[str, Any],
        autonomy_level: str,
    ) -> uuid.UUID:
        run_id = uuid6.uuid7()
        self._record(
            "create_run",
            intent=intent,
            config=config,
            autonomy_level=autonomy_level,
        )
        self._run_statuses[run_id] = "running"
        return run_id

    async def transition_run(
        self,
        run_id: uuid.UUID,
        new_status: str,
        reason: str | None = None,
    ) -> None:
        self._record(
            "transition_run",
            run_id=run_id,
            new_status=new_status,
            reason=reason,
        )
        self._run_statuses[run_id] = new_status

    async def create_task(
        self,
        run_id: uuid.UUID,
        parent_task_id: uuid.UUID | None,
        name: str,
        task_type: str,
        config: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        task_id = uuid6.uuid7()
        self._record(
            "create_task",
            run_id=run_id,
            parent_task_id=parent_task_id,
            name=name,
            task_type=task_type,
            config=config,
        )
        self._task_statuses[task_id] = "created"
        return task_id

    async def transition_task(
        self,
        task_id: uuid.UUID,
        new_status: str,
        reason: str | None = None,
    ) -> None:
        self._record(
            "transition_task",
            task_id=task_id,
            new_status=new_status,
            reason=reason,
        )
        self._task_statuses[task_id] = new_status

    async def create_task_attempt(
        self, task_id: uuid.UUID, attempt: int,
    ) -> uuid.UUID:
        attempt_id = uuid6.uuid7()
        self._record(
            "create_task_attempt",
            task_id=task_id,
            attempt=attempt,
        )
        return attempt_id

    async def complete_task_attempt(  # noqa: PLR0913
        self,
        attempt_id: uuid.UUID,
        agent_output: str,
        structured_output: dict[str, Any] | None,
        check_result: dict[str, Any] | None,
        check_verdict: str | None,
        session_id: uuid.UUID | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost_usd: Decimal,
        duration_seconds: float,
    ) -> None:
        self._record(
            "complete_task_attempt",
            attempt_id=attempt_id,
            agent_output=agent_output,
            check_verdict=check_verdict,
            cost_usd=cost_usd,
            duration_seconds=duration_seconds,
        )

    async def fail_task_attempt(  # noqa: PLR0913
        self,
        attempt_id: uuid.UUID,
        error: str,
        session_id: uuid.UUID | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost_usd: Decimal,
        duration_seconds: float,
    ) -> None:
        self._record(
            "fail_task_attempt",
            attempt_id=attempt_id,
            error=error,
        )

    async def write_event(
        self,
        run_id: uuid.UUID,
        event_type: str,
        data: dict[str, Any],
        task_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        event_id = uuid6.uuid7()
        self._record(
            "write_event",
            run_id=run_id,
            event_type=event_type,
            data=data,
        )
        return event_id

    async def write_transcript_entry(  # noqa: PLR0913
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        turn: int,
        role: str,
        content: str,
        tool_calls: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
    ) -> None:
        self._record(
            "write_transcript_entry",
            session_id=session_id,
            role=role,
            content=content,
        )

    async def write_coherence_summary(
        self,
        run_id: uuid.UUID,
        summary: str,
    ) -> None:
        self._record(
            "write_coherence_summary",
            run_id=run_id,
            summary=summary,
        )

    async def create_inbox_item(
        self,
        run_id: uuid.UUID,
        decision_type: str,
        question: str,
        options: list[dict[str, Any]],
        **kwargs: object,
    ) -> uuid.UUID:
        item_id = uuid6.uuid7()
        self._record(
            "create_inbox_item",
            run_id=run_id,
            decision_type=decision_type,
            question=question,
            options=options,
            **kwargs,
        )
        return item_id

    async def write_lesson(
        self, **kwargs: object,
    ) -> None:
        self._record("write_lesson", **kwargs)

    async def write_constraint(
        self, **kwargs: object,
    ) -> None:
        self._record("write_constraint", **kwargs)

    async def write_context_diff(
        self,
        attempt_id: uuid.UUID,
        pre_refinement: str,
        refinement_diff: str,
    ) -> None:
        self._record(
            "write_context_diff",
            attempt_id=attempt_id,
            pre_refinement=pre_refinement,
            refinement_diff=refinement_diff,
        )

    async def create_iteration(
        self,
        task_id: uuid.UUID,
        index: int,
        item_value: object,
    ) -> uuid.UUID:
        iteration_id = uuid6.uuid7()
        self._record(
            "create_iteration",
            task_id=task_id,
            index=index,
            item_value=item_value,
            iteration_id=iteration_id,
        )
        return iteration_id

    async def complete_iteration(
        self,
        iteration_id: uuid.UUID,
        output: str | None,
        structured_output: dict[str, Any] | None,
        check_results: list[dict[str, Any]] | None,
    ) -> None:
        self._record(
            "complete_iteration",
            iteration_id=iteration_id,
            output=output,
            structured_output=structured_output,
            check_results=check_results,
        )

    async def fail_iteration(
        self,
        iteration_id: uuid.UUID,
        error: str,
    ) -> None:
        self._record(
            "fail_iteration",
            iteration_id=iteration_id,
            error=error,
        )

    async def subscribe_run_control(
        self,
        run_id: uuid.UUID,
        callback: Any,  # noqa: ANN401
    ) -> None:
        self._control_callback = callback
        self._record(
            "subscribe_run_control",
            run_id=run_id,
        )

    async def unsubscribe_run_control(
        self,
        run_id: uuid.UUID,
    ) -> None:
        self._control_callback = None
        self._record(
            "unsubscribe_run_control",
            run_id=run_id,
        )

    def get_calls(self, method: str) -> list[dict[str, Any]]:
        return [
            kwargs for m, kwargs in self.calls if m == method
        ]


class MockTransport:
    """Configurable transport mock for all test scenarios.

    Modes:
    - Event sequence mode (session tests): set_events() / set_resume_events()
    - LLM simulation mode (scheduler tests): auto_execute_tools=True
    - No-tools mode: auto_execute_tools=False, no events set
    """

    def __init__(
        self,
        response_text: str = "Mock response",
        auto_execute_tools: bool = False,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.resume_calls: list[dict[str, Any]] = []
        self._event_sequences: list[list[Event]] = []
        self._resume_event_sequences: list[list[Event]] = []
        self._response_text = response_text
        self._auto_execute_tools = auto_execute_tools
        self._call_count = 0

    def set_events(self, *sequences: list[Event]) -> None:
        self._event_sequences = list(sequences)

    def set_resume_events(self, *sequences: list[Event]) -> None:
        self._resume_event_sequences = list(sequences)

    async def send(  # noqa: PLR0913
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        self.calls.append({
            "message": message,
            "model": model,
            "system_prompt": system_prompt,
            "tools": tools,
            "session_id": session_id,
            "stream_deltas": stream_deltas,
        })
        self._call_count += 1

        # If event sequences were set, use them (session test mode)
        if self._event_sequences:
            events = self._event_sequences.pop(0)
            for event in events:
                yield event
            return

        # Auto-execute tools mode (scheduler test mode)
        sid = session_id or str(uuid6.uuid7())
        if self._auto_execute_tools:
            tool_map = {t.name: t for t in tools}
            task_id_match = re.search(
                r"Your task ID is ([0-9a-f-]+)", message,
            )
            task_id_str = (
                task_id_match.group(1)
                if task_id_match
                else "unknown"
            )

            if "start_task" in tool_map:
                try:
                    start_result = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                except ToolError as e:
                    start_result = f"Error: {e}"
                yield ToolUse(
                    tool_name="start_task",
                    input={"task_id": task_id_str},
                    output=start_result,
                    status="success",
                )

            if "end_task" in tool_map:
                try:
                    end_result = await tool_map[
                        "end_task"
                    ].execute(
                        {"message": self._response_text},
                    )
                except ToolError as e:
                    end_result = f"Error: {e}"
                yield ToolUse(
                    tool_name="end_task",
                    input={
                        "message": self._response_text,
                    },
                    output=end_result,
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
                text=self._response_text,
                session_id=sid,
                total_input_tokens=10,
                total_output_tokens=5,
                total_reasoning_tokens=0,
                total_cache_read_tokens=0,
                total_cache_write_tokens=0,
                tool_calls=2,
            )
            return

        # No-tools mode (default, just yields finish + result)
        yield StepFinish(
            reason="end_turn",
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        yield Result(
            text=self._response_text,
            session_id=sid,
            total_input_tokens=10,
            total_output_tokens=5,
            total_reasoning_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            tool_calls=0,
        )

    async def resume(  # noqa: PLR0913
        self,
        continuation: Continuation,
        await_result: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        self.resume_calls.append({
            "continuation": continuation,
            "await_result": await_result,
            "model": model,
            "system_prompt": system_prompt,
            "tools": tools,
            "stream_deltas": stream_deltas,
        })
        events = (
            self._resume_event_sequences.pop(0)
            if self._resume_event_sequences
            else []
        )
        for event in events:
            yield event
