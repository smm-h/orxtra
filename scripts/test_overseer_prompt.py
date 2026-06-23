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

from orxtra.protocols import Confirmation, Tool, ToolOutput
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
    prompt_file = ROOT / "overseer" / "src" / "orxtra" / "overseer" / "prompts" / "overseer_base.md"
    return prompt_file.read_text()


# -- Mock tools ---------------------------------------------------------------


async def _mock_execute(args: dict[str, Any]) -> ToolOutput[Confirmation]:
    """Mock tool executor -- acknowledges the call without doing real work."""
    result = json.dumps({"status": "ok", "message": "Tool call acknowledged (mock)"})
    return ToolOutput(data=Confirmation(message=result), text=result)


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
                        "description": "Workflow name",
                    },
                    "description": {
                        "type": "string",
                        "description": "What this workflow accomplishes",
                    },
                    "goals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Goals the workflow must achieve",
                    },
                    "postchecks": {
                        "type": "array",
                        "description": "Post-check executions to run after the workflow",
                    },
                    "budget": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Budget in USD for this workflow",
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
                        "description": "Task name",
                    },
                    "agent": {
                        "type": "string",
                        "description": "Agent definition to execute this task",
                    },
                    "task_prompt": {
                        "type": "string",
                        "description": "Prompt describing what the task should accomplish",
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Timeout in seconds",
                    },
                    "context_refinement": {
                        "type": "boolean",
                        "description": "Whether to refine context before execution",
                    },
                    "prechecks": {
                        "type": "array",
                        "description": "Pre-check executions to run before the task",
                    },
                    "postchecks": {
                        "type": "array",
                        "description": "Post-check executions to run after the task",
                    },
                    "variable_values": {
                        "type": "object",
                        "description": "Variable substitutions for the task prompt",
                    },
                    "budget": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Budget in USD for this task",
                    },
                    "write_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Paths this task is allowed to write to",
                    },
                    "category": {
                        "type": "string",
                        "description": "Task category for agent resolution",
                    },
                    "retry": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Number of retry attempts on failure",
                    },
                    "retry_resume": {
                        "type": "boolean",
                        "description": "Whether retries resume from failure point",
                    },
                    "retry_inject_failure": {
                        "type": "boolean",
                        "description": "Whether to inject failure context on retry",
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Task IDs this task depends on",
                    },
                },
                "required": ["name", "agent", "task_prompt", "timeout", "context_refinement"],
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
                        "title": "Path",
                        "type": "string",
                    },
                    "offset": {
                        "anyOf": [
                            {"minimum": 1, "type": "integer"},
                            {"type": "null"},
                        ],
                        "default": None,
                        "title": "Offset",
                    },
                    "limit": {
                        "anyOf": [
                            {"minimum": 1, "type": "integer"},
                            {"type": "null"},
                        ],
                        "default": None,
                        "title": "Limit",
                    },
                    "full": {
                        "anyOf": [
                            {"type": "boolean"},
                            {"type": "null"},
                        ],
                        "default": None,
                        "title": "Full",
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
                        "title": "Agent",
                        "type": "string",
                    },
                    "question": {
                        "minLength": 1,
                        "title": "Question",
                        "type": "string",
                    },
                    "variable_values": {
                        "anyOf": [
                            {
                                "additionalProperties": {"type": "string"},
                                "type": "object",
                            },
                            {"type": "null"},
                        ],
                        "default": None,
                        "title": "Variable Values",
                    },
                },
                "required": ["agent", "question"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="record_decision",
            description="Record a decision with rationale in the decisions table.",
            parameters={
                "type": "object",
                "properties": {
                    "decision_type": {
                        "type": "string",
                        "description": "Category of the decision",
                    },
                    "choice": {
                        "type": "object",
                        "description": "The decision being made (structured)",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this decision was made",
                    },
                },
                "required": ["decision_type", "choice"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="record_assumption",
            description=(
                "Record an assumption, optionally creating"
                " an inbox item for verification."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The assumption text",
                    },
                    "scope": {
                        "type": "string",
                        "description": "Scope of the assumption",
                    },
                    "create_inbox_item": {
                        "type": "boolean",
                        "description": "Whether to create an inbox item for verification",
                    },
                },
                "required": ["text", "scope", "create_inbox_item"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="add_constraint",
            description="Add a mechanical or advisory constraint.",
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Constraint type (e.g. tests_pass, lint_clean)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Human-readable constraint description",
                    },
                    "tier": {
                        "type": "string",
                        "description": "Constraint tier (mechanical or advisory)",
                    },
                    "args": {
                        "type": "object",
                        "description": "Parameters for the constraint",
                    },
                },
                "required": ["kind", "text", "tier"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="create_inbox_item",
            description="Create a human inbox item for escalation.",
            parameters={
                "type": "object",
                "properties": {
                    "decision_type": {
                        "type": "string",
                        "description": "Category of the decision",
                    },
                    "question": {
                        "type": "string",
                        "description": "The question for the human",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Available options",
                    },
                    "assumed_option": {
                        "type": "string",
                        "description": "The option to proceed with if no response",
                    },
                    "work_proceeding": {
                        "type": "string",
                        "description": "What work continues while awaiting answer",
                    },
                    "contradiction_impact": {
                        "type": "string",
                        "description": "Impact if the assumption is wrong",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for categorizing the inbox item",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "Deadline for the human response",
                    },
                    "answer_event": {
                        "type": "string",
                        "description": "Event name to fire when the item is answered",
                    },
                },
                "required": [
                    "decision_type", "question", "options",
                    "assumed_option", "work_proceeding",
                    "contradiction_impact",
                ],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="write_lesson",
            description="Write to the cross-run knowledge base.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The lesson text",
                    },
                    "relevance_tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for lesson retrieval",
                    },
                    "permanent": {
                        "type": "boolean",
                        "description": "Whether the lesson persists across all runs",
                    },
                    "source_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Source files relevant to this lesson",
                    },
                },
                "required": ["text", "relevance_tags", "permanent"],
                "additionalProperties": False,
            },
            execute=_mock_execute,
        ),
        Tool(
            name="update_workflow_status",
            description="Update the Overseer's health assessment of a workflow.",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_id": {
                        "type": "string",
                        "description": "UUID of the workflow",
                    },
                    "current_step": {
                        "type": "string",
                        "description": "Current step description",
                    },
                    "health": {
                        "type": "string",
                        "description": "Health status (healthy, degraded, failing)",
                    },
                },
                "required": ["workflow_id", "health"],
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
