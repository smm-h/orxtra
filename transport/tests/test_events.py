from __future__ import annotations

import dataclasses

import pytest
from orxt.transport._events import (
    ApiRetry,
    ContentBlock,
    Error,
    Result,
    StepFinish,
    StepStart,
    StreamDelta,
    Text,
    Thinking,
    ToolUse,
    Usage,
)


class TestContentBlock:
    def test_text_block(self) -> None:
        block = ContentBlock(type="text", text="hello")
        assert block.type == "text"
        assert block.text == "hello"
        assert block.tool_use_id is None

    def test_tool_use_block(self) -> None:
        block = ContentBlock(
            type="tool_use",
            tool_use_id="tu_123",
            tool_name="read_file",
            tool_input={"path": "/home/user/test"},
        )
        assert block.type == "tool_use"
        assert block.tool_name == "read_file"
        assert block.tool_input == {"path": "/home/user/test"}

    def test_frozen(self) -> None:
        block = ContentBlock(type="text", text="hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            block.text = "world"  # type: ignore[misc]


class TestUsage:
    def test_defaults(self) -> None:
        usage = Usage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.reasoning_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0

    def test_values(self) -> None:
        usage = Usage(input_tokens=100, output_tokens=50, reasoning_tokens=10)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reasoning_tokens == 10

    def test_frozen(self) -> None:
        usage = Usage()
        with pytest.raises(dataclasses.FrozenInstanceError):
            usage.input_tokens = 5  # type: ignore[misc]


class TestStepStart:
    def test_construction(self) -> None:
        event = StepStart(session_id="abc-123")
        assert event.session_id == "abc-123"

    def test_frozen(self) -> None:
        event = StepStart(session_id="abc")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.session_id = "xyz"  # type: ignore[misc]


class TestText:
    def test_construction(self) -> None:
        event = Text(text="Hello world")
        assert event.text == "Hello world"

    def test_frozen(self) -> None:
        event = Text(text="hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.text = "world"  # type: ignore[misc]


class TestStreamDelta:
    def test_construction(self) -> None:
        event = StreamDelta(text="tok")
        assert event.text == "tok"

    def test_frozen(self) -> None:
        event = StreamDelta(text="a")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.text = "b"  # type: ignore[misc]


class TestThinking:
    def test_construction(self) -> None:
        event = Thinking(text="let me think...")
        assert event.text == "let me think..."


class TestToolUse:
    def test_construction(self) -> None:
        event = ToolUse(
            tool_name="read_file",
            input={"path": "/foo"},
            output="file contents",
            status="success",
        )
        assert event.tool_name == "read_file"
        assert event.input == {"path": "/foo"}
        assert event.output == "file contents"
        assert event.status == "success"
        assert event.error is None
        assert event.duration_ms == 0

    def test_error_fields(self) -> None:
        event = ToolUse(
            tool_name="write",
            input={},
            output="",
            status="error",
            error="Permission denied",
            duration_ms=42,
        )
        assert event.status == "error"
        assert event.error == "Permission denied"
        assert event.duration_ms == 42

    def test_frozen(self) -> None:
        event = ToolUse(tool_name="t", input={}, output="", status="success")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.status = "error"  # type: ignore[misc]


class TestStepFinish:
    def test_defaults(self) -> None:
        event = StepFinish(reason="end_turn")
        assert event.reason == "end_turn"
        assert event.input_tokens == 0
        assert event.output_tokens == 0

    def test_with_tokens(self) -> None:
        event = StepFinish(
            reason="end_turn",
            input_tokens=100,
            output_tokens=200,
            reasoning_tokens=50,
            cache_read_tokens=10,
            cache_write_tokens=5,
        )
        assert event.input_tokens == 100
        assert event.cache_write_tokens == 5


class TestApiRetry:
    def test_construction(self) -> None:
        event = ApiRetry(
            attempt=1,
            max_retries=3,
            delay_ms=1000,
            status_code=429,
            error="rate limited",
        )
        assert event.attempt == 1
        assert event.max_retries == 3
        assert event.delay_ms == 1000
        assert event.status_code == 429
        assert event.error == "rate limited"


class TestError:
    def test_construction(self) -> None:
        event = Error(name="api_error", message="something broke")
        assert event.name == "api_error"
        assert event.message == "something broke"
        assert event.metadata is None

    def test_with_metadata(self) -> None:
        event = Error(
            name="api_error",
            message="bad request",
            metadata={"status_code": 400},
        )
        assert event.metadata == {"status_code": 400}


class TestResult:
    def test_defaults(self) -> None:
        event = Result(text="Hello", session_id="sess-1")
        assert event.text == "Hello"
        assert event.session_id == "sess-1"
        assert event.total_input_tokens == 0
        assert event.total_output_tokens == 0
        assert event.total_reasoning_tokens == 0
        assert event.total_cache_read_tokens == 0
        assert event.total_cache_write_tokens == 0
        assert event.tool_calls == 0

    def test_with_tokens(self) -> None:
        event = Result(
            text="Done",
            session_id="s",
            total_input_tokens=500,
            total_output_tokens=300,
            total_reasoning_tokens=100,
            total_cache_read_tokens=50,
            total_cache_write_tokens=25,
            tool_calls=3,
        )
        assert event.total_input_tokens == 500
        assert event.tool_calls == 3
