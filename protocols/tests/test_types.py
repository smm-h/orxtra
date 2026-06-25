from __future__ import annotations

from decimal import Decimal

import pytest
from orxtra.protocols import (
    SEVERITY_ORDER,
    BudgetExhaustionPolicy,
    CheckResult,
    ErrorCategory,
    ScriptExecution,
    Severity,
    TaskSpec,
    TaskState,
    WorkflowExecution,
)
from pydantic import ValidationError

# -- TaskSpec tests --


class TestTaskSpecMinimalAgent:
    def test_valid_minimal_agent(self) -> None:
        spec = TaskSpec(
            name="t1",
            agent="coder",
            task_prompt="Do X",
            timeout=300,
            context_refinement=True,
        )
        assert spec.name == "t1"
        assert spec.agent == "coder"
        assert spec.task_prompt == "Do X"
        assert spec.timeout == 300
        assert spec.context_refinement is True
        assert spec.prechecks == []
        assert spec.postchecks == []
        assert spec.subtasks is None
        assert spec.callable is None
        assert spec.retry == 0


class TestTaskSpecCallable:
    def test_valid_callable(self) -> None:
        spec = TaskSpec(name="t1", callable="mymod.run")
        assert spec.callable == "mymod.run"
        assert spec.agent is None
        assert spec.task_prompt is None


class TestTaskSpecSubtasks:
    def test_subtasks(self) -> None:
        child = TaskSpec(
            name="child",
            agent="a",
            task_prompt="p",
            timeout=60,
            context_refinement=False,
        )
        parent = TaskSpec(name="parent", subtasks=[child])
        assert parent.subtasks is not None
        assert len(parent.subtasks) == 1
        assert parent.subtasks[0].name == "child"


class TestTaskSpecMultipleExecutionModes:
    def test_both_agent_and_callable(self) -> None:
        # TaskSpec allows both at model level; scheduler validates later
        spec = TaskSpec(name="t1", agent="a", task_prompt="p", callable="x")
        assert spec.agent == "a"
        assert spec.callable == "x"


class TestTaskSpecTimeoutNone:
    def test_missing_timeout(self) -> None:
        spec = TaskSpec(name="t1", agent="a", task_prompt="p")
        assert spec.timeout is None


class TestTaskSpecExtraField:
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaskSpec(name="t1", extra_field="x")  # type: ignore[call-arg]


class TestTaskSpecWrongType:
    def test_wrong_type_for_name(self) -> None:
        with pytest.raises(ValidationError):
            TaskSpec(name=123)  # type: ignore[arg-type]


class TestTaskSpecDeepNesting:
    def test_three_level_nesting(self) -> None:
        grandchild = TaskSpec(name="grandchild", callable="gc.run")
        child = TaskSpec(name="child", subtasks=[grandchild])
        parent = TaskSpec(name="parent", subtasks=[child])
        assert parent.subtasks is not None
        assert parent.subtasks[0].subtasks is not None
        assert parent.subtasks[0].subtasks[0].name == "grandchild"


class TestTaskSpecFrozen:
    def test_mutation_rejected(self) -> None:
        spec = TaskSpec(name="t1")
        with pytest.raises(ValidationError):
            spec.name = "t2"  # type: ignore[misc]


# -- WorkflowExecution tests --


class TestWorkflowExecutionWithTasks:
    def test_workflow_with_task_list(self) -> None:
        t1 = TaskSpec(name="step1", callable="s1.run")
        t2 = TaskSpec(name="step2", agent="a", task_prompt="do step2")
        wf = WorkflowExecution(
            name="wf1",
            description="A workflow",
            tasks=[t1, t2],
            postchecks=[],
        )
        assert wf.name == "wf1"
        assert wf.description == "A workflow"
        assert len(wf.tasks) == 2
        assert wf.budget is None


class TestWorkflowExecutionWithPostchecks:
    def test_postchecks_with_script_execution(self) -> None:
        check = ScriptExecution(callable="checks.verify")
        wf = WorkflowExecution(
            name="wf2",
            description="Workflow with checks",
            tasks=[TaskSpec(name="t1", callable="t1.run")],
            postchecks=[check],
        )
        assert len(wf.postchecks) == 1
        assert isinstance(wf.postchecks[0], ScriptExecution)


class TestWorkflowExecutionBudget:
    def test_budget_field(self) -> None:
        wf = WorkflowExecution(
            name="wf3",
            description="Budget workflow",
            tasks=[],
            postchecks=[],
            budget=Decimal("10.50"),
        )
        assert wf.budget == Decimal("10.50")


# -- Enum tests --


class TestTaskStateEnum:
    def test_all_ten_values(self) -> None:
        expected = {
            "created",
            "prechecking",
            "active",
            "postchecking",
            "completed",
            "precheck_failed",
            "postcheck_failed",
            "escalated",
            "cancelled",
            "suspended",
        }
        actual = {s.value for s in TaskState}
        assert actual == expected
        assert len(TaskState) == 10


class TestBudgetExhaustionPolicyEnum:
    def test_all_four_values(self) -> None:
        expected = {
            "block_new",
            "cancel_all",
            "timeout_grace",
            "unlimited",
        }
        actual = {p.value for p in BudgetExhaustionPolicy}
        assert actual == expected
        assert len(BudgetExhaustionPolicy) == 4


class TestErrorCategoryEnum:
    def test_all_seven_values(self) -> None:
        expected = {
            "infra",
            "context_limit",
            "parse",
            "flaky",
            "build_env",
            "logic",
            "unclassified",
        }
        actual = {e.value for e in ErrorCategory}
        assert actual == expected
        assert len(ErrorCategory) == 7


class TestSeverityOrdering:
    def test_severity_order(self) -> None:
        assert SEVERITY_ORDER[Severity.CRITICAL] == 4
        assert SEVERITY_ORDER[Severity.MAJOR] == 3
        assert SEVERITY_ORDER[Severity.MINOR] == 2
        assert SEVERITY_ORDER[Severity.NIT] == 1
        assert SEVERITY_ORDER[Severity.CRITICAL] > SEVERITY_ORDER[Severity.MAJOR]
        assert SEVERITY_ORDER[Severity.MAJOR] > SEVERITY_ORDER[Severity.MINOR]
        assert SEVERITY_ORDER[Severity.MINOR] > SEVERITY_ORDER[Severity.NIT]


# -- CheckResult tests --


class TestCheckResultNoFix:
    def test_check_result_no_fix(self) -> None:
        cr = CheckResult(passed=True, message="All good")
        assert cr.passed is True
        assert cr.message == "All good"
        assert cr.fix is None
        assert cr.details is None


class TestCheckResultWithFix:
    def test_check_result_with_fix(self) -> None:
        def my_fix() -> None:
            pass

        cr = CheckResult(passed=False, message="Failed", fix=my_fix)
        assert cr.passed is False
        assert cr.fix is my_fix


class TestCheckResultFrozen:
    def test_mutation_raises(self) -> None:
        cr = CheckResult(passed=True, message="ok")
        with pytest.raises(AttributeError):
            cr.passed = False  # type: ignore[misc]


