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
