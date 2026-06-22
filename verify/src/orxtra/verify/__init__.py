from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-verify")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.verify._execution import execute_check
from orxtra.verify._runner import run_checks
from orxtra.verify._types import (
    SEVERITY_ORDER,
    CheckAgentContext,
    CheckContext,
    CheckExecutor,
    CheckIssue,
    CheckResult,
    CheckVerdict,
    CriterionReview,
    Severity,
)

__all__ = [
    "__version__",
    "SEVERITY_ORDER",
    "CheckAgentContext",
    "CheckContext",
    "CheckExecutor",
    "CheckIssue",
    "CheckResult",
    "CheckVerdict",
    "CriterionReview",
    "Severity",
    "execute_check",
    "run_checks",
]
