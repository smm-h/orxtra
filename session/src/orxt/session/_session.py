from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orxt.protocols import Tool
    from orxt.trace import TraceWriter
    from orxt.transport import Event, Transport

from orxt.transport import Result, StepFinish, ToolUse


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

    async def send(self, message: str) -> AsyncIterator[Event]:
        self.turn_count += 1
        current_turn = self.turn_count

        result_text: str = ""
        tool_calls_list: list[dict[str, Any]] = []
        session_id_captured = False

        stream = self._transport.send(
            message,
            model=self._model,
            system_prompt=self._system_prompt,
            tools=self._tools,
            session_id=self._session_id,
        )

        async for event in stream:
            if isinstance(event, Result):
                if self._session_id is None:
                    self._session_id = event.session_id
                result_text = event.text

                # Write user transcript entry now that we have session_id
                if not session_id_captured:
                    session_id_captured = True
                    await self._trace_writer.write_transcript_entry(
                        session_id=uuid.UUID(self._session_id),
                        run_id=self._run_id,
                        turn=current_turn,
                        role="user",
                        content=message,
                    )

            elif isinstance(event, StepFinish):
                self.total_input_tokens += event.input_tokens
                self.total_output_tokens += event.output_tokens
                self.total_reasoning_tokens += event.reasoning_tokens
                self.total_cache_read_tokens += event.cache_read_tokens
                self.total_cache_write_tokens += event.cache_write_tokens

            elif isinstance(event, ToolUse):
                tool_calls_list.append({
                    "tool_name": event.tool_name,
                    "input": event.input,
                    "output": event.output,
                    "status": event.status,
                })

            yield event

        # Write assistant transcript entry
        if self._session_id is not None and session_id_captured:
            tokens_dict: dict[str, Any] = {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "reasoning_tokens": self.total_reasoning_tokens,
                "cache_read_tokens": self.total_cache_read_tokens,
                "cache_write_tokens": self.total_cache_write_tokens,
            }
            await self._trace_writer.write_transcript_entry(
                session_id=uuid.UUID(self._session_id),
                run_id=self._run_id,
                turn=current_turn,
                role="assistant",
                content=result_text,
                tool_calls={"calls": tool_calls_list} if tool_calls_list else None,
                tokens=tokens_dict,
            )

    def resume_id(self) -> str:
        if self._session_id is None:
            msg = "No session ID available — no messages have been sent yet"
            raise RuntimeError(msg)
        return self._session_id
