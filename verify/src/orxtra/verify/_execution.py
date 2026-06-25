from __future__ import annotations

import importlib
import traceback

from orxtra.protocols import (
    SEVERITY_ORDER,
    AgentExecution,
    CheckContext,
    CheckExecutor,
    CheckIssue,
    CheckResult,
    CheckVerdict,
    Execution,
    ScriptExecution,
    WorkflowExecution,
)


def _format_mechanical_results(results: list[CheckResult] | None) -> str:
    if not results:
        return "No mechanical check results."
    lines: list[str] = []
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        lines.append(f"- [{tag}] {r.message}")
    return "\n".join(lines)


async def _run_script(
    execution: ScriptExecution,
    ctx: CheckContext,
) -> CheckResult:
    parts = execution.callable.split(":")
    if len(parts) != 2:  # noqa: PLR2004
        return CheckResult(
            passed=False,
            message=(
                f"Invalid callable path: {execution.callable!r}"
                " (expected 'module:function')"
            ),
        )
    module_path, func_name = parts
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        return CheckResult(
            passed=False,
            message=f"Module not found: {module_path!r}",
            details={"error": str(e)},
        )
    func = getattr(module, func_name, None)
    if func is None:
        return CheckResult(
            passed=False,
            message=f"Function {func_name!r} not found in module {module_path!r}",
        )
    try:
        result: CheckResult = await func(ctx)
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            passed=False,
            message=f"Script check raised {type(e).__name__}: {e}",
            details={"error": str(e), "traceback": traceback.format_exc()},
        )
    else:
        return result


async def _run_agent(
    execution: AgentExecution,
    ctx: CheckContext,
    executor: CheckExecutor,
    mechanical_results: list[CheckResult] | None,
) -> CheckResult:
    variable_values: dict[str, str] = {
        "task": execution.task,
        "agent_output": ctx.agent_output or "",
        "mechanical_results": _format_mechanical_results(mechanical_results),
        "task_name": ctx.task_name,
        "attempt": str(ctx.attempt),
        "notepad": "",
    }
    for key, value in ctx.variables.items():
        variable_values[f"var_{key}"] = str(value)

    response = await executor.run_consult(
        agent=execution.agent,
        question=execution.task,
        variable_values=variable_values,
    )

    try:
        verdict = CheckVerdict.model_validate_json(response)
    except Exception as e:  # noqa: BLE001
        return CheckResult(
            passed=False,
            message=f"Agent returned invalid verdict: {e}",
            details={"raw_response": response},
        )

    threshold_value = SEVERITY_ORDER[execution.block_threshold]
    derived_issues: list[CheckIssue] = []
    for issue in verdict.issues:
        blocking = SEVERITY_ORDER[issue.severity] >= threshold_value
        derived_issues.append(
            CheckIssue(
                severity=issue.severity,
                file=issue.file,
                line_range=issue.line_range,
                description=issue.description,
                blocking=blocking,
            )
        )

    passed = not any(issue.blocking for issue in derived_issues)
    return CheckResult(
        passed=passed,
        message=verdict.summary,
        details={
            "issues": [issue.model_dump() for issue in derived_issues],
            "criteria_review": [cr.model_dump() for cr in verdict.criteria_review],
        },
    )


async def execute_check(
    execution: Execution,
    ctx: CheckContext,
    executor: CheckExecutor,
    mechanical_results: list[CheckResult] | None = None,
) -> CheckResult:
    if isinstance(execution, ScriptExecution):
        return await _run_script(execution, ctx)
    if isinstance(execution, AgentExecution):
        return await _run_agent(execution, ctx, executor, mechanical_results)
    if isinstance(execution, WorkflowExecution):
        return await executor.run_workflow_check(execution)
    msg = f"Unknown execution type: {type(execution).__name__}"
    raise TypeError(msg)
