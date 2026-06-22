from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxtra.protocols._constraints import ConstraintTier
from orxtra.protocols._results import Confirmation, ToolOutput
from orxtra.protocols._tool import Tool, ToolError
from orxtra.protocols._tools import (
    AddConstraintParams,
    AddConstraintResult,
    CreateInboxItemParams,
    CreateInboxItemResult,
    RecordAssumptionParams,
    RecordAssumptionResult,
    RecordDecisionParams,
    RecordDecisionResult,
    UpdateWorkflowStatusParams,
    WriteLessonParams,
    WriteLessonResult,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from orxtra.trace import TraceWriter


def _validation_error(e: Exception) -> str:
    return json.dumps({"error": "validation_error", "details": str(e)})


def make_record_decision_tool(
    trace_writer: TraceWriter, run_id: UUID,
) -> Tool:
    async def execute(args: dict[str, Any]) -> ToolOutput[RecordDecisionResult]:
        try:
            params = RecordDecisionParams.model_validate(args)
        except ValidationError as e:
            raise ToolError(_validation_error(e)) from e
        decision_id = await trace_writer.write_decision(
            run_id=run_id,
            decision_type=params.decision_type,
            choice=json.dumps(params.choice),
            rationale=params.rationale,
        )
        result = RecordDecisionResult(decision_id=decision_id)
        return ToolOutput(data=result, text=result.model_dump_json())

    return Tool(
        name="record_decision",
        description="Record a decision with rationale in the decisions table.",
        parameters=RecordDecisionParams.model_json_schema(),
        execute=execute,
    )


def make_add_constraint_tool(
    trace_writer: TraceWriter, run_id: UUID,
) -> Tool:
    async def execute(args: dict[str, Any]) -> ToolOutput[AddConstraintResult]:
        try:
            coerced = {**args}
            if "tier" in coerced and isinstance(coerced["tier"], str):
                coerced["tier"] = ConstraintTier(coerced["tier"])
            params = AddConstraintParams.model_validate(coerced)
        except (ValidationError, ValueError) as e:
            raise ToolError(_validation_error(e)) from e
        constraint_id = await trace_writer.write_constraint(
            run_id=run_id,
            text=params.text,
            tier=params.tier.value,
            kind=params.kind,
            args=params.args,
        )
        result = AddConstraintResult(constraint_id=constraint_id)
        return ToolOutput(data=result, text=result.model_dump_json())

    return Tool(
        name="add_constraint",
        description="Add a mechanical or advisory constraint.",
        parameters=AddConstraintParams.model_json_schema(),
        execute=execute,
    )


def make_record_assumption_tool(
    trace_writer: TraceWriter, run_id: UUID,
) -> Tool:
    async def execute(args: dict[str, Any]) -> ToolOutput[RecordAssumptionResult]:
        try:
            params = RecordAssumptionParams.model_validate(args)
        except ValidationError as e:
            raise ToolError(_validation_error(e)) from e

        inbox_item_id: UUID | None = None
        if params.create_inbox_item:
            inbox_item_id = await trace_writer.create_inbox_item(
                run_id=run_id,
                decision_type="assumption_verification",
                question=f"Please verify assumption: {params.text}",
                options=[
                    {"label": "confirm", "description": "Assumption is correct"},
                    {"label": "deny", "description": "Assumption is incorrect"},
                ],
                assumed_option="confirm",
                work_proceeding=f"Proceeding with assumption: {params.text}",
                contradiction_impact=(
                    "May need to revise approach if assumption is wrong."
                ),
            )

        assumption_id = await trace_writer.write_assumption(
            run_id=run_id,
            text=params.text,
            scope=params.scope,
            inbox_item_id=inbox_item_id,
        )
        result = RecordAssumptionResult(
            assumption_id=assumption_id,
            inbox_item_id=inbox_item_id,
        )
        return ToolOutput(data=result, text=result.model_dump_json())

    return Tool(
        name="record_assumption",
        description=(
            "Record an assumption, optionally creating"
            " an inbox item for verification."
        ),
        parameters=RecordAssumptionParams.model_json_schema(),
        execute=execute,
    )


def make_create_inbox_item_tool(
    trace_writer: TraceWriter, run_id: UUID,
) -> Tool:
    async def execute(args: dict[str, Any]) -> ToolOutput[CreateInboxItemResult]:
        try:
            params = CreateInboxItemParams.model_validate(args)
        except ValidationError as e:
            raise ToolError(_validation_error(e)) from e
        item_id = await trace_writer.create_inbox_item(
            run_id=run_id,
            decision_type=params.decision_type,
            question=params.question,
            options=params.options,
            assumed_option=params.assumed_option,
            work_proceeding=params.work_proceeding,
            contradiction_impact=params.contradiction_impact,
            tags=params.tags,
            deadline=None,
            answer_event=params.answer_event,
        )
        result = CreateInboxItemResult(item_id=item_id)
        return ToolOutput(data=result, text=result.model_dump_json())

    return Tool(
        name="create_inbox_item",
        description="Create a human inbox item for escalation.",
        parameters=CreateInboxItemParams.model_json_schema(),
        execute=execute,
    )


def make_write_lesson_tool(
    trace_writer: TraceWriter, run_id: UUID,
) -> Tool:
    async def execute(args: dict[str, Any]) -> ToolOutput[WriteLessonResult]:
        try:
            params = WriteLessonParams.model_validate(args)
        except ValidationError as e:
            raise ToolError(_validation_error(e)) from e
        lesson_id = await trace_writer.write_lesson(
            run_id=run_id,
            text=params.text,
            relevance_tags=params.relevance_tags,
            permanent=params.permanent,
            source_files=params.source_files or None,
        )
        result = WriteLessonResult(lesson_id=lesson_id)
        return ToolOutput(data=result, text=result.model_dump_json())

    return Tool(
        name="write_lesson",
        description="Write to the cross-run knowledge base.",
        parameters=WriteLessonParams.model_json_schema(),
        execute=execute,
    )


def make_update_workflow_status_tool(
    trace_writer: TraceWriter,
) -> Tool:
    async def execute(args: dict[str, Any]) -> ToolOutput[Confirmation]:
        try:
            params = UpdateWorkflowStatusParams.model_validate(args)
        except ValidationError as e:
            raise ToolError(_validation_error(e)) from e
        await trace_writer.update_workflow_status(
            workflow_id=UUID(params.workflow_id),
            current_step=params.current_step or "",
            health=params.health,
        )
        text = json.dumps({"status": "updated"})
        return ToolOutput(data=Confirmation(message="updated"), text=text)

    return Tool(
        name="update_workflow_status",
        description="Update the Overseer's health assessment of a workflow.",
        parameters=UpdateWorkflowStatusParams.model_json_schema(),
        execute=execute,
    )
