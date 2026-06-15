from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import (
    MockCheckExecutor,
    make_check_context,
    make_failing_verdict,
    make_mixed_verdict,
    make_passing_verdict,
)
from orxt.protocols._execution import (
    AgentExecution,
    CheckResult,
    ScriptExecution,
    Severity,
)
from orxt.protocols._task import WorkflowExecution
from orxt.verify._execution import execute_check

if TYPE_CHECKING:
    from orxt.protocols._checks import CheckContext


class TestScriptChecks:
    async def test_script_check_passes(
        self, ctx: CheckContext,
    ) -> None:
        execution = ScriptExecution(
            callable="tests.sample_checks:always_pass",
        )
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is True
        assert result.message == "Check passed"

    async def test_script_check_fails(
        self, ctx: CheckContext,
    ) -> None:
        execution = ScriptExecution(
            callable="tests.sample_checks:always_fail",
        )
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is False
        assert result.message == "Check failed"

    async def test_script_check_with_fix_callable(
        self, ctx: CheckContext,
    ) -> None:
        execution = ScriptExecution(
            callable="tests.sample_checks:fixable_fail",
        )
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is False
        assert result.fix is not None

    async def test_invalid_callable_path_no_colon(
        self, ctx: CheckContext,
    ) -> None:
        execution = ScriptExecution(callable="no_colon_here")
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is False
        assert "Invalid callable path" in result.message

    async def test_module_not_found(
        self, ctx: CheckContext,
    ) -> None:
        execution = ScriptExecution(
            callable="nonexistent.module:func",
        )
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is False
        assert "Module not found" in result.message
        assert result.details is not None
        assert "error" in result.details

    async def test_function_not_found(
        self, ctx: CheckContext,
    ) -> None:
        execution = ScriptExecution(
            callable="tests.sample_checks:nonexistent_func",
        )
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is False
        assert "not found in module" in result.message

    async def test_script_raises_exception(
        self, ctx: CheckContext,
    ) -> None:
        execution = ScriptExecution(
            callable="tests.sample_checks:raises_error",
        )
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is False
        assert "RuntimeError" in result.message
        assert "Intentional error" in result.message
        assert result.details is not None
        assert "traceback" in result.details

    async def test_script_receives_correct_context(
        self, ctx: CheckContext,
    ) -> None:
        custom_ctx = make_check_context(
            variables={"foo": "bar"},
            task_name="custom-task",
        )
        execution = ScriptExecution(
            callable="tests.sample_checks:returns_variables",
        )
        result = await execute_check(
            execution, custom_ctx, MockCheckExecutor(),
        )
        assert result.passed is True
        assert result.details is not None
        assert result.details["variables"] == {"foo": "bar"}
        assert result.details["task_name"] == "custom-task"

    async def test_async_script_check_works(
        self, ctx: CheckContext,
    ) -> None:
        # All sample checks are async, so this verifies async execution
        execution = ScriptExecution(
            callable="tests.sample_checks:check_with_details",
        )
        result = await execute_check(
            execution, ctx, MockCheckExecutor(),
        )
        assert result.passed is True
        assert result.details == {"key": "value"}


class TestAgentChecks:
    async def test_agent_check_passes(
        self, ctx: CheckContext,
    ) -> None:
        executor = MockCheckExecutor(
            consult_response=make_passing_verdict(),
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.MAJOR,
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is True
        assert result.message == "All checks passed"

    async def test_agent_check_fails_blocking_issue(
        self, ctx: CheckContext,
    ) -> None:
        executor = MockCheckExecutor(
            consult_response=make_failing_verdict(
                severity="major",
            ),
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.MAJOR,
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is False
        assert result.details is not None
        issues = result.details["issues"]
        assert len(issues) == 1
        assert issues[0]["blocking"] is True

    async def test_agent_check_mixed_severities(
        self, ctx: CheckContext,
    ) -> None:
        # Mixed verdict has: critical, nit, minor
        # With block_threshold=MAJOR, only critical should block
        executor = MockCheckExecutor(
            consult_response=make_mixed_verdict(),
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.MAJOR,
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is False  # critical blocks
        issues = result.details["issues"]
        blocking_issues = [
            i for i in issues if i["blocking"]
        ]
        non_blocking = [
            i for i in issues if not i["blocking"]
        ]
        assert len(blocking_issues) == 1  # only critical
        assert blocking_issues[0]["severity"] == "critical"
        assert len(non_blocking) == 2  # nit and minor

    async def test_block_threshold_nit_everything_blocks(
        self, ctx: CheckContext,
    ) -> None:
        executor = MockCheckExecutor(
            consult_response=make_mixed_verdict(),
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.NIT,
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is False
        issues = result.details["issues"]
        assert all(i["blocking"] for i in issues)

    async def test_block_threshold_critical_only_critical_blocks(
        self, ctx: CheckContext,
    ) -> None:
        executor = MockCheckExecutor(
            consult_response=make_mixed_verdict(),
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.CRITICAL,
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is False
        issues = result.details["issues"]
        blocking_issues = [
            i for i in issues if i["blocking"]
        ]
        assert len(blocking_issues) == 1
        assert blocking_issues[0]["severity"] == "critical"

    async def test_agent_context_includes_var_prefix(
        self, ctx: CheckContext,
    ) -> None:
        custom_ctx = make_check_context(
            variables={"project": "orxt", "lang": "python"},
        )
        executor = MockCheckExecutor(
            consult_response=make_passing_verdict(),
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.MAJOR,
        )
        await execute_check(execution, custom_ctx, executor)
        call = executor.consult_calls[0]
        assert call["variable_values"]["var_project"] == "orxt"
        assert call["variable_values"]["var_lang"] == "python"

    async def test_mechanical_results_formatted(
        self, ctx: CheckContext,
    ) -> None:
        prior_results = [
            CheckResult(passed=True, message="Lint passed"),
            CheckResult(
                passed=False, message="Tests failed",
            ),
        ]
        executor = MockCheckExecutor(
            consult_response=make_passing_verdict(),
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.MAJOR,
        )
        await execute_check(
            execution,
            ctx,
            executor,
            mechanical_results=prior_results,
        )
        call = executor.consult_calls[0]
        mech = call["variable_values"]["mechanical_results"]
        assert "- [PASS] Lint passed" in mech
        assert "- [FAIL] Tests failed" in mech

    async def test_agent_returns_invalid_json(
        self, ctx: CheckContext,
    ) -> None:
        executor = MockCheckExecutor(
            consult_response="not json at all",
        )
        execution = AgentExecution(
            agent="reviewer",
            task="Review the code",
            block_threshold=Severity.MAJOR,
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is False
        assert "invalid verdict" in result.message.lower()
        assert result.details is not None
        assert result.details["raw_response"] == "not json at all"


class TestWorkflowChecks:
    async def test_workflow_check_passes(
        self, ctx: CheckContext,
    ) -> None:
        workflow_result = CheckResult(
            passed=True, message="Workflow completed",
        )
        executor = MockCheckExecutor(
            workflow_result=workflow_result,
        )
        execution = WorkflowExecution(
            name="test-workflow",
            description="A test workflow",
            tasks=[],
            postchecks=[],
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is True
        assert result.message == "Workflow completed"
        assert len(executor.workflow_calls) == 1

    async def test_workflow_check_fails(
        self, ctx: CheckContext,
    ) -> None:
        workflow_result = CheckResult(
            passed=False,
            message="Workflow failed",
            details={
                "step": "integration-test",
                "error": "timeout",
            },
        )
        executor = MockCheckExecutor(
            workflow_result=workflow_result,
        )
        execution = WorkflowExecution(
            name="test-workflow",
            description="A test workflow",
            tasks=[],
            postchecks=[],
        )
        result = await execute_check(execution, ctx, executor)
        assert result.passed is False
        assert result.message == "Workflow failed"
        assert result.details is not None
        assert result.details["step"] == "integration-test"
