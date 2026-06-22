"""Test the Overseer's base system prompt against a real LLM.

Sends the Overseer system prompt + a RunStarted event to gpt-4o-mini
and prints the full response, including any tool calls the model makes.

Usage:
    cd /path/to/orxtra && uv run python scripts/test_overseer_prompt.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from orxtra.protocols import Tool
from orxtra.transport import (
    ApiRetry,
    Error,
    Result,
    RetryPolicy,
    StepFinish,
    StepStart,
    StreamDelta,
    StreamToolUse,
    Text,
    Thinking,
    ToolUse,
    Transport,
)
from orxtra.transport.providers._openai import OpenAIProvider

ROOT = Path(__file__).resolve().parent.parent

# -- Load API key from .env --------------------------------------------------


def _load_api_key() -> str:
    env_file = ROOT / ".env"
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("OPENAI_API_KEY=") and not line.endswith("="):
            return line.split("=", 1)[1]
    msg = "OPENAI_API_KEY not found or empty in .env"
    raise RuntimeError(msg)


# -- Load system prompt -------------------------------------------------------


def _load_system_prompt() -> str:
    prompt_file = ROOT / "overseer" / "prompts" / "overseer_base.md"
    return prompt_file.read_text()


# -- Mock tools ---------------------------------------------------------------


async def _mock_execute(args: dict[str, Any]) -> str:
    """Mock tool executor -- acknowledges the call without doing real work."""
    return json.dumps({"status": "ok", "message": "Tool call acknowledged (mock)"})


def _make_mock_tools() -> list[Tool]:
    return [
        Tool(
            name="create_workflow",
            description=(
                "Create a new workflow (a task that contains subtasks). "
                "Workflows define the structure of work."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the workflow",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of what this workflow accomplishes",
                    },
                    "goals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of goals for this workflow",
                    },
                },
                "required": ["name", "description", "goals"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="create_task",
            description=(
                "Create a new task within a workflow. Tasks declare dependencies, "
                "pre-checks, and post-checks."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the task",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Agent to assign the task to",
                    },
                    "task_prompt": {
                        "type": "string",
                        "description": "Detailed prompt/instructions for the agent",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds",
                    },
                },
                "required": ["name", "agent", "task_prompt", "timeout"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="read",
            description=(
                "Read file contents. Large files return a preview with "
                "opt-in full retrieval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="consult",
            description=(
                "Consult a specialist agent for research before making a decision. "
                "The consulted agent receives a read-only toolset and returns "
                "structured findings."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "description": "Name of the specialist agent to consult",
                    },
                    "question": {
                        "type": "string",
                        "description": "The question to ask the specialist",
                    },
                },
                "required": ["agent", "question"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="record_decision",
            description="Record a strategic decision with rationale.",
            parameters={
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "description": "The decision being made",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this decision was made",
                    },
                },
                "required": ["decision", "rationale"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="record_assumption",
            description="Record an assumption you are making.",
            parameters={
                "type": "object",
                "properties": {
                    "assumption": {
                        "type": "string",
                        "description": "The assumption being recorded",
                    },
                },
                "required": ["assumption"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="add_constraint",
            description=(
                "Add a constraint to the current run. Constraints have a kind "
                "and args."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Constraint type (e.g. tests_pass, lint_clean)",
                    },
                    "args": {
                        "type": "object",
                        "description": "Parameters for the constraint",
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="create_inbox_item",
            description=(
                "Create a question for the human operator with an assumed option."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question for the human",
                    },
                    "assumed_option": {
                        "type": "string",
                        "description": "The option to proceed with if no response",
                    },
                },
                "required": ["question", "assumed_option"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
    ]


# -- Main --------------------------------------------------------------------

MODEL = "gpt-4o-mini"

USER_MESSAGE = json.dumps({
    "event_type": "RunStarted",
    "intent": (
        "Add a health check endpoint to the API server that returns 200 OK "
        "with a JSON body containing uptime and version"
    ),
    "config_snapshot": {},
})


async def main() -> None:
    api_key = _load_api_key()
    system_prompt = _load_system_prompt()
    tools = _make_mock_tools()

    provider = OpenAIProvider(api_key=api_key)
    retry_policy = RetryPolicy(
        max_retries=2,
        backoff_base_seconds=1.0,
        backoff_max_seconds=30.0,
        jitter=True,
    )
    transport = Transport(provider=provider, retry_policy=retry_policy)

    print(f"Model: {MODEL}")
    print(f"System prompt: {len(system_prompt)} chars")
    print(f"Tools: {[t.name for t in tools]}")
    print(f"User message: {USER_MESSAGE[:120]}...")
    print()
    print("=" * 72)
    print("SENDING REQUEST...")
    print("=" * 72)
    print()

    async for event in transport.send(
        USER_MESSAGE,
        model=MODEL,
        system_prompt=system_prompt,
        tools=tools,
    ):
        if isinstance(event, StepStart):
            print(f"[StepStart] session_id={event.session_id}")
            print()

        elif isinstance(event, StreamDelta):
            # Print streaming text as it arrives
            print(event.text, end="", flush=True)

        elif isinstance(event, Text):
            # Full text block (emitted after streaming completes)
            print()
            print()
            print(f"[Text] {event.text}")

        elif isinstance(event, Thinking):
            print(f"[Thinking] {event.text}")

        elif isinstance(event, StreamToolUse):
            print()
            print(f"[StreamToolUse] {event.tool_name}")
            print(f"  id: {event.tool_use_id}")
            print(f"  args: {json.dumps(event.tool_input, indent=2)}")
            print()

        elif isinstance(event, ToolUse):
            status = event.status
            if event.error:
                status += f" ({event.error})"
            print(
                f"[ToolUse] {event.tool_name} -> {status} "
                f"({event.duration_ms}ms)"
            )
            if event.output:
                print(f"  output: {event.output}")
            print()

        elif isinstance(event, StepFinish):
            print(f"[StepFinish] reason={event.reason}")
            print(f"  input_tokens:  {event.input_tokens}")
            print(f"  output_tokens: {event.output_tokens}")
            if event.reasoning_tokens:
                print(f"  reasoning_tokens: {event.reasoning_tokens}")
            if event.cache_read_tokens:
                print(f"  cache_read_tokens: {event.cache_read_tokens}")
            print()

        elif isinstance(event, Result):
            print("=" * 72)
            print("RESULT")
            print("=" * 72)
            print(f"  Total input tokens:  {event.total_input_tokens}")
            print(f"  Total output tokens: {event.total_output_tokens}")
            print(f"  Tool calls:          {event.tool_calls}")
            if event.text:
                print()
                print("Final text:")
                print(event.text)
            print()

        elif isinstance(event, ApiRetry):
            print(
                f"[ApiRetry] attempt {event.attempt}/{event.max_retries} "
                f"status={event.status_code} delay={event.delay_ms}ms"
            )
            print(f"  error: {event.error}")
            print()

        elif isinstance(event, Error):
            print(f"[ERROR] {event.name}: {event.message}")
            if event.metadata:
                print(f"  metadata: {event.metadata}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
