from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.protocols._execution import CheckResult

if TYPE_CHECKING:
    from orxtra.protocols._checks import CheckContext


async def always_pass(ctx: CheckContext) -> CheckResult:
    return CheckResult(passed=True, message="Check passed")


async def always_fail(ctx: CheckContext) -> CheckResult:
    return CheckResult(passed=False, message="Check failed")


async def check_with_details(ctx: CheckContext) -> CheckResult:
    return CheckResult(
        passed=True,
        message="Detailed check passed",
        details={"key": "value"},
    )


async def raises_error(ctx: CheckContext) -> CheckResult:
    msg = "Intentional error"
    raise RuntimeError(msg)


async def returns_variables(ctx: CheckContext) -> CheckResult:
    return CheckResult(
        passed=True,
        message="Variables received",
        details={"variables": ctx.variables, "task_name": ctx.task_name},
    )


_fix_call_count = 0


async def fixable_fail(ctx: CheckContext) -> CheckResult:
    global _fix_call_count  # noqa: PLW0603
    if _fix_call_count > 0:
        _fix_call_count = 0
        return CheckResult(passed=True, message="Fixed and passed")
    return CheckResult(
        passed=False,
        message="Fixable failure",
        fix=_do_fix,
    )


async def _do_fix(ctx: CheckContext) -> None:
    global _fix_call_count  # noqa: PLW0603
    _fix_call_count += 1


async def fixable_still_fails(ctx: CheckContext) -> CheckResult:
    return CheckResult(
        passed=False,
        message="Still failing after fix",
        fix=_do_nothing_fix,
    )


async def _do_nothing_fix(ctx: CheckContext) -> None:
    pass


async def fix_raises(ctx: CheckContext) -> CheckResult:
    return CheckResult(
        passed=False,
        message="Fix will raise",
        fix=_raising_fix,
    )


async def _raising_fix(ctx: CheckContext) -> None:
    msg = "Fix failed"
    raise RuntimeError(msg)
