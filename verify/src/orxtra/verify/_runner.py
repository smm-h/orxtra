from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.verify._execution import execute_check

if TYPE_CHECKING:
    from orxtra.protocols._checks import CheckContext, CheckExecutor
    from orxtra.protocols._execution import CheckResult
    from orxtra.protocols._task import Execution


async def run_checks(
    checks: list[Execution],
    ctx: CheckContext,
    phase: str,
    executor: CheckExecutor,
) -> list[CheckResult]:
    _ = phase
    results: list[CheckResult] = []

    for check in checks:
        result = await execute_check(
            check, ctx, executor, mechanical_results=results,
        )

        if result.passed:
            results.append(result)
            continue

        if result.fix is not None:
            try:
                await result.fix(ctx)
            except Exception:  # noqa: BLE001
                results.append(result)
                return results

            rerun = await execute_check(
                check, ctx, executor, mechanical_results=results,
            )
            results.append(rerun)
            if not rerun.passed:
                return results
            continue

        results.append(result)
        return results

    return results
