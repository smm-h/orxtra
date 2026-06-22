from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orxtra.protocols import Tool
    from orxtra.trace import TraceWriter
    from orxtra.transport import Event, Transport

from orxtra.transport import Continuation, Result, SessionSuspended, StepFinish, ToolUse


class Session:
    def __init__(  # noqa: PLR0913
        self,
        transport: Transport,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        trace_writer: TraceWriter,
        run_id: uuid.UUID,
        session_id: str | None = None,
    ) -> None:
        self._transport = transport
        self._model = model
        self._system_prompt = system_prompt
        self._tools = tools
        self._trace_writer = trace_writer
        self._run_id = run_id
        self._session_id = session_id
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0
        self.turn_count: int = 0

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def tools(self) -> list[Tool]:
        return self._tools

    def _accumulate_tokens(self, event: StepFinish) -> None:
        """Add token counts from a step-finish event to session totals."""
        self.total_input_tokens += event.input_tokens
        self.total_output_tokens += event.output_tokens
        self.total_reasoning_tokens += event.reasoning_tokens
        self.total_cache_read_tokens += event.cache_read_tokens
        self.total_cache_write_tokens += event.cache_write_tokens

    def _compute_token_delta(
        self, snapshot: dict[str, int],
    ) -> dict[str, Any]:
        """Compute token deltas between current totals and a snapshot."""
        return {
            "input_tokens": self.total_input_tokens - snapshot["input"],
            "output_tokens": self.total_output_tokens - snapshot["output"],
            "reasoning_tokens": self.total_reasoning_tokens - snapshot["reasoning"],
            "cache_read_tokens": self.total_cache_read_tokens - snapshot["cache_read"],
            "cache_write_tokens": (
                self.total_cache_write_tokens - snapshot["cache_write"]
            ),
        }

    def _token_snapshot(self) -> dict[str, int]:
        """Capture current token totals for later delta computation."""
        return {
            "input": self.total_input_tokens,
            "output": self.total_output_tokens,
            "reasoning": self.total_reasoning_tokens,
            "cache_read": self.total_cache_read_tokens,
            "cache_write": self.total_cache_write_tokens,
        }

    async def _try_capture_session(
        self,
        session_id: str | None,
        captured: bool,
        turn: int,
        message: str,
    ) -> bool:
        """Capture session ID if not yet known.

        Writes user transcript on first capture.
        Returns the updated captured flag.
        """
        if session_id is not None and self._session_id is None:
            self._session_id = session_id
        if not captured and self._session_id is not None:
            await self._trace_writer.write_transcript_entry(
                session_id=uuid.UUID(self._session_id),
                run_id=self._run_id,
                turn=turn,
                role="user",
                content=message,
            )
            return True
        return captured

    async def send(
        self, message: str,
    ) -> AsyncIterator[Event]:
        self.turn_count += 1
        current_turn = self.turn_count

        result_text: str = ""
        tool_calls_list: list[dict[str, Any]] = []
        session_id_captured = False
        snapshot = self._token_snapshot()

        stream = self._transport.send(
            message,
            model=self._model,
            system_prompt=self._system_prompt,
            tools=self._tools,
            session_id=self._session_id,
        )

        async for event in stream:
            if isinstance(event, Result):
                result_text = event.text
                session_id_captured = await self._try_capture_session(
                    event.session_id, session_id_captured, current_turn, message,
                )

            elif isinstance(event, StepFinish):
                self._accumulate_tokens(event)

            elif isinstance(event, ToolUse):
                tool_calls_list.append({
                    "tool_name": event.tool_name,
                    "input": event.input,
                    "output": event.output,
                    "status": event.status,
                })

            elif isinstance(event, SessionSuspended):
                session_id_captured = await self._try_capture_session(
                    event.session_id, session_id_captured, current_turn, message,
                )

            yield event

        # Write assistant transcript entry
        if self._session_id is not None and session_id_captured:
            await self._trace_writer.write_transcript_entry(
                session_id=uuid.UUID(self._session_id),
                run_id=self._run_id,
                turn=current_turn,
                role="assistant",
                content=result_text,
                tool_calls={"calls": tool_calls_list} if tool_calls_list else None,
                tokens=self._compute_token_delta(snapshot),
            )

    async def resume(
        self, continuation: Continuation, result: str
    ) -> AsyncIterator[Event]:
        """Resume from suspension. Delegates to transport.resume()."""
        self.turn_count += 1
        current_turn = self.turn_count

        result_text: str = ""
        tool_calls_list: list[dict[str, Any]] = []
        snapshot = self._token_snapshot()

        stream = self._transport.resume(
            continuation,
            result,
            model=self._model,
            system_prompt=self._system_prompt,
            tools=self._tools,
        )

        async for event in stream:
            if isinstance(event, Result):
                result_text = event.text

            elif isinstance(event, StepFinish):
                self._accumulate_tokens(event)

            elif isinstance(event, ToolUse):
                tool_calls_list.append({
                    "tool_name": event.tool_name,
                    "input": event.input,
                    "output": event.output,
                    "status": event.status,
                })

            yield event

        # Write transcript entries for the resume turn
        if self._session_id is not None:
            await self._trace_writer.write_transcript_entry(
                session_id=uuid.UUID(self._session_id),
                run_id=self._run_id,
                turn=current_turn,
                role="user",
                content=f"[resume: {result}]",
            )
            await self._trace_writer.write_transcript_entry(
                session_id=uuid.UUID(self._session_id),
                run_id=self._run_id,
                turn=current_turn,
                role="assistant",
                content=result_text,
                tool_calls={"calls": tool_calls_list} if tool_calls_list else None,
                tokens=self._compute_token_delta(snapshot),
            )

    def resume_id(self) -> str:
        if self._session_id is None:
            msg = "No session ID available — no messages have been sent yet"
            raise RuntimeError(msg)
        return self._session_id

    async def close(self) -> None:
        """Write final transcript entry and clean up."""
        # No-op if no session exists
        if self._session_id is None:
            return
        # Could write a final transcript marker here

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
