from enum import StrEnum


class ErrorCategory(StrEnum):
    INFRA = "infra"
    CONTEXT_LIMIT = "context_limit"
    PARSE = "parse"
    FLAKY = "flaky"
    BUILD_ENV = "build_env"
    LOGIC = "logic"
    UNCLASSIFIED = "unclassified"
