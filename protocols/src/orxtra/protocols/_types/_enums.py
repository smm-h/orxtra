from __future__ import annotations

from enum import StrEnum


class ConstraintTier(StrEnum):
    MECHANICAL = "mechanical"
    ADVISORY = "advisory"


class ConstraintKind(StrEnum):
    TESTS_PASS = "tests_pass"  # noqa: S105
    LINT_CLEAN = "lint_clean"
    NO_REMOVED_EXPORTS = "no_removed_exports"
    NO_CHANGED_SIGNATURES = "no_changed_signatures"
    NO_NEW_DEPENDENCIES = "no_new_dependencies"
    NO_NEW_FILES_OUTSIDE = "no_new_files_outside"


EXPENSIVE_CONSTRAINTS: frozenset[ConstraintKind] = frozenset({
    ConstraintKind.TESTS_PASS,
    ConstraintKind.LINT_CLEAN,
})

ALWAYS_ACTIVE_CONSTRAINTS: frozenset[ConstraintKind] = frozenset({
    ConstraintKind.TESTS_PASS,
    ConstraintKind.LINT_CLEAN,
})


class ErrorCategory(StrEnum):
    INFRA = "infra"
    CONTEXT_LIMIT = "context_limit"
    PARSE = "parse"
    FLAKY = "flaky"
    BUILD_ENV = "build_env"
    LOGIC = "logic"
    UNCLASSIFIED = "unclassified"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.MAJOR: 3,
    Severity.MINOR: 2,
    Severity.NIT: 1,
}
