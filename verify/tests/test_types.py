from __future__ import annotations

from orxtra.protocols._execution import Severity


class TestReExports:
    def test_all_reexports_accessible(self) -> None:
        from orxtra.verify import (  # noqa: PLC0415
            SEVERITY_ORDER,
            CheckAgentContext,
            CheckContext,
            CheckExecutor,
            CheckIssue,
            CheckResult,
            CheckVerdict,
            CriterionReview,
            Severity,
            execute_check,
            run_checks,
        )
        assert CheckResult is not None
        assert CheckVerdict is not None
        assert CheckIssue is not None
        assert CriterionReview is not None
        assert CheckContext is not None
        assert CheckAgentContext is not None
        assert CheckExecutor is not None
        assert Severity is not None
        assert SEVERITY_ORDER is not None
        assert execute_check is not None
        assert run_checks is not None


class TestSeverityOrdering:
    def test_severity_order(self) -> None:
        from orxtra.verify import SEVERITY_ORDER  # noqa: PLC0415

        assert SEVERITY_ORDER[Severity.CRITICAL] > SEVERITY_ORDER[Severity.MAJOR]
        assert SEVERITY_ORDER[Severity.MAJOR] > SEVERITY_ORDER[Severity.MINOR]
        assert SEVERITY_ORDER[Severity.MINOR] > SEVERITY_ORDER[Severity.NIT]

    def test_severity_order_values(self) -> None:
        from orxtra.verify import SEVERITY_ORDER  # noqa: PLC0415

        assert SEVERITY_ORDER[Severity.CRITICAL] == 4
        assert SEVERITY_ORDER[Severity.MAJOR] == 3
        assert SEVERITY_ORDER[Severity.MINOR] == 2
        assert SEVERITY_ORDER[Severity.NIT] == 1
