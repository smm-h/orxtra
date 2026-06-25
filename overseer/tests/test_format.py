from __future__ import annotations

import json
import uuid

from orxtra.overseer._format import format_event
from orxtra.protocols import (
    BudgetExhausted,
    CheckResult,
    EscalationPayload,
    RunStarted,
    TaskContext,
    TaskFailed,
)


class TestFormatEventRunStarted:
    def test_produces_valid_json(self) -> None:
        event = RunStarted(intent="deploy", config_snapshot={"key": "val"})
        result = format_event(event)
        parsed = json.loads(result)
        assert parsed["event_type"] == "RunStarted"
        assert parsed["intent"] == "deploy"
        assert parsed["config_snapshot"] == {"key": "val"}


class TestFormatEventBudgetExhausted:
    def test_handles_uuid(self) -> None:
        wf_id = uuid.uuid4()
        event = BudgetExhausted(workflow_id=wf_id)
        result = format_event(event)
        parsed = json.loads(result)
        assert parsed["event_type"] == "BudgetExhausted"
        assert parsed["workflow_id"] == wf_id.hex


class TestFormatEventTaskFailed:
    def test_handles_nested_escalation_payload(self) -> None:
        task_id = uuid.uuid4()
        run_id = uuid.uuid4()
        ctx = TaskContext(
            variables={"v": "1"},
            run_id=run_id,
            task_name="t1",
            task_id=task_id,
            attempt=1,
            prior_attempts=None,
            notepad_content="notes",
            parent_task_id=None,
            nesting_depth=0,
        )
        payload = EscalationPayload(
            task_name="t1",
            task_id=task_id,
            agent_name="coder",
            attempts=2,
            failed_checks=[CheckResult(passed=False, message="check failed")],
            agent_summary="summary",
            context=ctx,
        )
        event = TaskFailed(
            task_id=task_id,
            task_name="t1",
            payload=payload,
        )
        result = format_event(event)
        parsed = json.loads(result)
        assert parsed["event_type"] == "TaskFailed"
        assert parsed["task_name"] == "t1"
        assert parsed["task_id"] == task_id.hex
        assert parsed["payload"]["agent_name"] == "coder"
        assert parsed["payload"]["attempts"] == 2
        assert parsed["payload"]["context"]["run_id"] == run_id.hex
        assert parsed["payload"]["failed_checks"][0]["passed"] is False
