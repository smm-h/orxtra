from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import MockCheckExecutor, make_check_context, make_passing_verdict
from orxt.protocols._execution import AgentExecution, ScriptExecution, Severity
from orxt.verify._runner import run_checks

if TYPE_CHECKING:
    from orxt.protocols._checks import CheckContext


class TestRunChecks:
    async def test_empty_check_list(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        results = await run_checks([], ctx, "pre", executor)
        assert results == []

    async def test_single_passing_check(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [ScriptExecution(callable="tests.sample_checks:always_pass")]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].passed is True

    async def test_single_failing_check(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [ScriptExecution(callable="tests.sample_checks:always_fail")]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].passed is False

    async def test_multiple_checks_all_pass(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [
            ScriptExecution(callable="tests.sample_checks:always_pass"),
            ScriptExecution(callable="tests.sample_checks:always_pass"),
            ScriptExecution(callable="tests.sample_checks:always_pass"),
        ]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 3
        assert all(r.passed for r in results)

    async def test_first_check_fails_short_circuit(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [
            ScriptExecution(callable="tests.sample_checks:always_fail"),
            ScriptExecution(callable="tests.sample_checks:always_pass"),
            ScriptExecution(callable="tests.sample_checks:always_pass"),
        ]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].passed is False

    async def test_second_check_fails(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [
            ScriptExecution(callable="tests.sample_checks:always_pass"),
            ScriptExecution(callable="tests.sample_checks:always_fail"),
            ScriptExecution(callable="tests.sample_checks:always_pass"),
        ]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is False

    async def test_fix_callable_check_fails_fix_runs_recheck_passes(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        # fixable_fail: first call fails with fix, after fix called, second call passes
        # Note: fixable_fail uses global state, so it must be the only fixable check
        # in this test
        checks = [ScriptExecution(callable="tests.sample_checks:fixable_fail")]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].message == "Fixed and passed"

    async def test_fix_callable_recheck_still_fails(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [ScriptExecution(callable="tests.sample_checks:fixable_still_fails")]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].passed is False

    async def test_fix_callable_only_called_once(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        # fixable_still_fails always fails, fix does nothing
        # The runner should try fix once, re-check once, then stop
        # We verify by checking the result -- if it tried more than once,
        # we'd see multiple results or different behavior
        checks = [
            ScriptExecution(callable="tests.sample_checks:fixable_still_fails"),
            ScriptExecution(callable="tests.sample_checks:always_pass"),
        ]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1  # short-circuited after fix failed
        assert results[0].passed is False

    async def test_fix_callable_raises_exception(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [ScriptExecution(callable="tests.sample_checks:fix_raises")]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].passed is False

    async def test_mix_of_script_and_agent_checks(
        self, ctx: CheckContext,
    ) -> None:
        executor = MockCheckExecutor(consult_response=make_passing_verdict())
        checks = [
            ScriptExecution(callable="tests.sample_checks:always_pass"),
            AgentExecution(
                agent="reviewer",
                task="Review the code",
                block_threshold=Severity.MAJOR,
            ),
        ]
        results = await run_checks(checks, ctx, "post", executor)
        assert len(results) == 2
        assert results[0].passed is True
        assert results[1].passed is True

    async def test_pre_check_phase_works(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [ScriptExecution(callable="tests.sample_checks:always_pass")]
        results = await run_checks(checks, ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].passed is True

    async def test_post_check_phase_works(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        checks = [ScriptExecution(callable="tests.sample_checks:always_pass")]
        results = await run_checks(checks, ctx, "post", executor)
        assert len(results) == 1
        assert results[0].passed is True

    async def test_context_passed_through(
        self, ctx: CheckContext, executor: MockCheckExecutor,
    ) -> None:
        custom_ctx = make_check_context(
            variables={"key": "val"},
            task_name="ctx-test",
        )
        checks = [ScriptExecution(callable="tests.sample_checks:returns_variables")]
        results = await run_checks(checks, custom_ctx, "pre", executor)
        assert len(results) == 1
        assert results[0].details is not None
        assert results[0].details["variables"] == {"key": "val"}
        assert results[0].details["task_name"] == "ctx-test"

    async def test_agent_check_after_script_gets_mechanical_results(
        self, ctx: CheckContext,
    ) -> None:
        executor = MockCheckExecutor(consult_response=make_passing_verdict())
        checks = [
            ScriptExecution(callable="tests.sample_checks:always_pass"),
            AgentExecution(
                agent="reviewer",
                task="Review the code",
                block_threshold=Severity.MAJOR,
            ),
        ]
        results = await run_checks(checks, ctx, "post", executor)
        assert len(results) == 2
        # The agent check should have received the script result as
        # mechanical_results
        call = executor.consult_calls[0]
        mech = call["variable_values"]["mechanical_results"]
        assert "- [PASS] Check passed" in mech
