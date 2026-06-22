from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from orxtra.protocols._execution import CheckResult

if TYPE_CHECKING:
    from orxtra.protocols._checks import CheckContext
    from orxtra.protocols._task import Execution


class MockCheckExecutor:
    def __init__(
        self,
        consult_response: str = "",
        workflow_result: CheckResult | None = None,
    ) -> None:
        self._consult_response = consult_response
        self._workflow_result = workflow_result or CheckResult(
            passed=True, message="Workflow passed",
        )
        self.consult_calls: list[dict[str, Any]] = []
        self.workflow_calls: list[Execution] = []

    async def run_consult(
        self,
        agent: str,
        question: str,
        variable_values: dict[str, str] | None = None,
    ) -> str:
        self.consult_calls.append({
            "agent": agent,
            "question": question,
            "variable_values": variable_values,
        })
        return self._consult_response

    async def run_workflow_check(
        self,
        execution: Execution,
    ) -> CheckResult:
        self.workflow_calls.append(execution)
        return self._workflow_result


def make_check_context(
    *,
    variables: dict[str, Any] | None = None,
    agent_output: str | None = None,
    task_name: str = "test-task",
    attempt: int = 1,
) -> CheckContext:
    from orxtra.protocols._checks import CheckContext  # noqa: PLC0415

    return CheckContext(
        variables=variables or {},
        agent_output=agent_output,
        run_id=uuid.uuid4(),
        session_id="test-session",
        task_name=task_name,
        task_id=uuid.uuid4(),
        attempt=attempt,
        parent_task_id=None,
    )


def make_passing_verdict() -> str:
    return json.dumps({
        "verdict": "pass",
        "issues": [],
        "criteria_review": [
            {"criterion": "test", "met": True, "evidence": "All good"},
        ],
        "summary": "All checks passed",
    })


def make_failing_verdict(
    *,
    severity: str = "major",
    blocking: bool = True,
    description: str = "Found a problem",
    summary: str = "Issues found",
) -> str:
    return json.dumps({
        "verdict": "fail",
        "issues": [
            {
                "severity": severity,
                "file": "test.py",
                "line_range": [1, 10],
                "description": description,
                "blocking": blocking,
            },
        ],
        "criteria_review": [
            {"criterion": "test", "met": False, "evidence": "Failed"},
        ],
        "summary": summary,
    })


def make_mixed_verdict(
    *,
    summary: str = "Mixed results",
) -> str:
    return json.dumps({
        "verdict": "fail",
        "issues": [
            {
                "severity": "critical",
                "file": "critical.py",
                "line_range": None,
                "description": "Critical issue",
                "blocking": True,
            },
            {
                "severity": "nit",
                "file": "style.py",
                "line_range": [5, 5],
                "description": "Style nit",
                "blocking": False,
            },
            {
                "severity": "minor",
                "file": None,
                "line_range": None,
                "description": "Minor issue",
                "blocking": False,
            },
        ],
        "criteria_review": [],
        "summary": summary,
    })


@pytest.fixture
def ctx() -> CheckContext:
    return make_check_context()


@pytest.fixture
def executor() -> MockCheckExecutor:
    return MockCheckExecutor()
