from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-protocols")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.protocols._autonomy import (
    AutonomyLevel,
    is_autonomous,
    requires_approval,
)
from orxtra.protocols._checks import (
    CheckAgentContext,
    CheckContext,
    CheckExecutor,
)
from orxtra.protocols._constraints import (
    ALWAYS_ACTIVE_CONSTRAINTS,
    EXPENSIVE_CONSTRAINTS,
    ConstraintKind,
    ConstraintTier,
)
from orxtra.protocols._errors import ErrorCategory
from orxtra.protocols._events import (
    BudgetExhausted,
    BudgetThresholdCrossed,
    HealthDegraded,
    InboxAnswered,
    InboxRejected,
    RunStarted,
    StructuralAdvisory,
    TaskEscalated,
    TaskFailed,
    format_event,
)
from orxtra.protocols._execution import (
    SEVERITY_ORDER,
    AgentExecution,
    CheckIssue,
    CheckResult,
    CheckVerdict,
    CriterionReview,
    ScriptExecution,
    Severity,
)
from orxtra.protocols._task import (
    AttemptSummary,
    BudgetExhaustionPolicy,
    EscalationPayload,
    Execution,
    TaskContext,
    TaskResult,
    TaskSpec,
    TaskState,
    WorkflowExecution,
)
from orxtra.protocols._results import (
    Confirmation,
    ConsultResponse,
    DiffResult,
    DirEntry,
    DirListing,
    ExecResult,
    FileContent,
    FileStat,
    GitOutput,
    GrepMatch,
    GrepResult,
    HttpResponse,
    Renderer,
    TaskLifecycleResult,
    ToolOutput,
)
from orxtra.protocols._tool import Tool, ToolError

__all__ = [
    "__version__",
    "ALWAYS_ACTIVE_CONSTRAINTS",
    "EXPENSIVE_CONSTRAINTS",
    "SEVERITY_ORDER",
    "AgentExecution",
    "AttemptSummary",
    "AutonomyLevel",
    "BudgetExhausted",
    "BudgetExhaustionPolicy",
    "BudgetThresholdCrossed",
    "CheckAgentContext",
    "CheckContext",
    "CheckExecutor",
    "CheckIssue",
    "CheckResult",
    "CheckVerdict",
    "Confirmation",
    "ConstraintKind",
    "ConstraintTier",
    "ConsultResponse",
    "CriterionReview",
    "DiffResult",
    "DirEntry",
    "DirListing",
    "ErrorCategory",
    "EscalationPayload",
    "ExecResult",
    "Execution",
    "FileContent",
    "FileStat",
    "GitOutput",
    "GrepMatch",
    "GrepResult",
    "HealthDegraded",
    "HttpResponse",
    "InboxAnswered",
    "InboxRejected",
    "Renderer",
    "RunStarted",
    "ScriptExecution",
    "Severity",
    "StructuralAdvisory",
    "TaskContext",
    "TaskEscalated",
    "TaskFailed",
    "TaskLifecycleResult",
    "TaskResult",
    "TaskSpec",
    "TaskState",
    "Tool",
    "ToolError",
    "ToolOutput",
    "WorkflowExecution",
    "format_event",
    "is_autonomous",
    "requires_approval",
]
