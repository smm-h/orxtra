from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import uuid6
from conftest import MockTraceWriter
from orxtra.overseer._tools import (
    make_add_constraint_tool,
    make_create_inbox_item_tool,
    make_record_assumption_tool,
    make_record_decision_tool,
    make_update_workflow_status_tool,
    make_write_lesson_tool,
)
from orxtra.protocols._tool import ToolError

if TYPE_CHECKING:
    from uuid import UUID


@pytest.fixture
def run_id() -> UUID:
    return uuid6.uuid7()


@pytest.fixture
def tw() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.mark.asyncio
async def test_record_decision_valid(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_record_decision_tool(tw, run_id)
    result = (await tool.execute({
        "decision_type": "architecture",
        "choice": {"approach": "modular"},
        "rationale": "Better separation of concerns",
    })).text
    parsed = json.loads(result)
    assert "decision_id" in parsed
    assert len(tw.calls) == 1
    assert tw.calls[0][0] == "write_decision"
    assert tw.calls[0][1]["decision_type"] == "architecture"


@pytest.mark.asyncio
async def test_record_decision_invalid(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_record_decision_tool(tw, run_id)
    with pytest.raises(ToolError):
        await tool.execute({"bad_field": "value"})


@pytest.mark.asyncio
async def test_add_constraint_mechanical(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_add_constraint_tool(tw, run_id)
    result = (await tool.execute({
        "kind": "no_removed_exports",
        "text": "All tests must pass",
        "tier": "mechanical",
    })).text
    parsed = json.loads(result)
    assert "constraint_id" in parsed
    assert tw.calls[0][0] == "write_constraint"
    assert tw.calls[0][1]["tier"] == "mechanical"
    assert tw.calls[0][1]["kind"] == "no_removed_exports"


@pytest.mark.asyncio
async def test_add_constraint_advisory(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_add_constraint_tool(tw, run_id)
    result = (await tool.execute({
        "kind": "code_style",
        "text": "Prefer small functions",
        "tier": "advisory",
    })).text
    parsed = json.loads(result)
    assert "constraint_id" in parsed
    assert tw.calls[0][1]["tier"] == "advisory"
    assert tw.calls[0][1]["kind"] == "code_style"


@pytest.mark.asyncio
async def test_record_assumption_with_inbox(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_record_assumption_tool(tw, run_id)
    result = (await tool.execute({
        "text": "The API supports pagination",
        "scope": "task",
        "create_inbox_item": True,
    })).text
    parsed = json.loads(result)
    assert "assumption_id" in parsed
    assert parsed["inbox_item_id"] is not None
    method_names = [c[0] for c in tw.calls]
    assert "create_inbox_item" in method_names
    assert "write_assumption" in method_names


@pytest.mark.asyncio
async def test_record_assumption_without_inbox(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_record_assumption_tool(tw, run_id)
    result = (await tool.execute({
        "text": "The database is PostgreSQL",
        "scope": "run",
        "create_inbox_item": False,
    })).text
    parsed = json.loads(result)
    assert "assumption_id" in parsed
    assert parsed["inbox_item_id"] is None
    assert len(tw.calls) == 1
    assert tw.calls[0][0] == "write_assumption"


@pytest.mark.asyncio
async def test_create_inbox_item_all_params(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_create_inbox_item_tool(tw, run_id)
    result = (await tool.execute({
        "decision_type": "architecture",
        "question": "Which database?",
        "options": [{"label": "pg"}, {"label": "mysql"}],
        "assumed_option": "pg",
        "work_proceeding": "Using pg for now",
        "contradiction_impact": "Would need migration",
        "tags": ["db"],
    })).text
    parsed = json.loads(result)
    assert "item_id" in parsed
    assert tw.calls[0][0] == "create_inbox_item"


@pytest.mark.asyncio
async def test_write_lesson_permanent(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_write_lesson_tool(tw, run_id)
    result = (await tool.execute({
        "text": "Always validate inputs",
        "relevance_tags": ["validation"],
        "permanent": True,
    })).text
    parsed = json.loads(result)
    assert "lesson_id" in parsed
    assert tw.calls[0][1]["permanent"] is True


@pytest.mark.asyncio
async def test_write_lesson_non_permanent(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_write_lesson_tool(tw, run_id)
    result = (await tool.execute({
        "text": "This approach worked for this task",
        "relevance_tags": ["approach"],
        "permanent": False,
    })).text
    parsed = json.loads(result)
    assert "lesson_id" in parsed
    assert tw.calls[0][1]["permanent"] is False


@pytest.mark.asyncio
async def test_update_workflow_status(
    tw: MockTraceWriter,
) -> None:
    tool = make_update_workflow_status_tool(tw)
    wf_id = str(uuid6.uuid7())
    result = (await tool.execute({
        "workflow_id": wf_id,
        "current_step": "testing",
        "health": "healthy",
    })).text
    parsed = json.loads(result)
    assert parsed["status"] == "updated"
    assert tw.calls[0][0] == "update_workflow_status"


def test_tool_names_and_descriptions(
    tw: MockTraceWriter,
) -> None:
    run_id = uuid6.uuid7()
    tools = [
        make_record_decision_tool(tw, run_id),
        make_add_constraint_tool(tw, run_id),
        make_record_assumption_tool(tw, run_id),
        make_create_inbox_item_tool(tw, run_id),
        make_write_lesson_tool(tw, run_id),
        make_update_workflow_status_tool(tw),
    ]
    for tool in tools:
        assert isinstance(tool.name, str)
        assert len(tool.name) > 0
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0


def test_tool_json_schema_params(
    tw: MockTraceWriter,
) -> None:
    run_id = uuid6.uuid7()
    tools = [
        make_record_decision_tool(tw, run_id),
        make_add_constraint_tool(tw, run_id),
        make_record_assumption_tool(tw, run_id),
        make_create_inbox_item_tool(tw, run_id),
        make_write_lesson_tool(tw, run_id),
        make_update_workflow_status_tool(tw),
    ]
    for tool in tools:
        assert isinstance(tool.parameters, dict)
        assert (
            "properties" in tool.parameters
            or "type" in tool.parameters
        )


@pytest.mark.asyncio
async def test_tool_rejects_invalid_types(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_record_decision_tool(tw, run_id)
    with pytest.raises(ToolError):
        await tool.execute({
            "decision_type": 123,
            "choice": "not_a_dict",
        })


@pytest.mark.asyncio
async def test_add_constraint_rejects_invalid_tier(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_add_constraint_tool(tw, run_id)
    with pytest.raises(ToolError):
        await tool.execute({
            "kind": "test",
            "text": "constraint",
            "tier": "invalid_tier",
        })


@pytest.mark.asyncio
async def test_write_lesson_rejects_extra_field(
    tw: MockTraceWriter, run_id: UUID,
) -> None:
    tool = make_write_lesson_tool(tw, run_id)
    with pytest.raises(ToolError):
        await tool.execute({
            "text": "lesson",
            "relevance_tags": ["tag"],
            "permanent": True,
            "extra_field": "not_allowed",
        })
