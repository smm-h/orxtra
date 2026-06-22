from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-tool")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.tool._consult_tool import CONSULT_STRIP_TOOLS, make_consult_tool
from orxtra.tool._exec_tool import make_exec_tool
from orxtra.tool._git_tool import make_git_tool
from orxtra.tool._http_tool import make_http_tool
from orxtra.tool._notepad_tool import make_notepad_tool
from orxtra.tool._path import PathError, check_write_scope, resolve_and_check
from orxtra.tool._pipeline import (
    FILE_MUTATION_TOOLS,
    compose,
    wrap_tool_with_pipeline,
    wrap_tools_for_session,
)
from orxtra.tool._preview import (
    FullRetrievalGuard,
    PreviewResult,
    check_and_preview,
)
from orxtra.tool._read_tools import (
    make_diff_tool,
    make_glob_tool,
    make_grep_tool,
    make_list_dir_tool,
    make_read_tool,
    make_stat_tool,
)
from orxtra.tool._shell_tool import make_shell_tool
from orxtra.tool._task_tools import (
    TaskSchedulerRef,
    make_await_task_tool,
    make_create_task_tool,
    make_create_wait_for_tool,
    make_create_workflow_tool,
    make_end_task_tool,
    make_start_task_tool,
)
from orxtra.tool._validation import validate_args
from orxtra.tool._write_integration import safe_read_for_write, safe_write
from orxtra.tool._write_tools import (
    make_copy_tool,
    make_delete_tool,
    make_edit_tool,
    make_mkdir_tool,
    make_move_tool,
    make_set_executable_tool,
    make_write_tool,
)

__all__ = [
    "__version__",
    "CONSULT_STRIP_TOOLS",
    "FILE_MUTATION_TOOLS",
    "FullRetrievalGuard",
    "PathError",
    "PreviewResult",
    "TaskSchedulerRef",
    "check_and_preview",
    "check_write_scope",
    "compose",
    "make_await_task_tool",
    "make_consult_tool",
    "make_copy_tool",
    "make_create_task_tool",
    "make_create_wait_for_tool",
    "make_create_workflow_tool",
    "make_delete_tool",
    "make_diff_tool",
    "make_edit_tool",
    "make_end_task_tool",
    "make_exec_tool",
    "make_git_tool",
    "make_glob_tool",
    "make_grep_tool",
    "make_http_tool",
    "make_list_dir_tool",
    "make_mkdir_tool",
    "make_move_tool",
    "make_notepad_tool",
    "make_read_tool",
    "make_set_executable_tool",
    "make_shell_tool",
    "make_start_task_tool",
    "make_stat_tool",
    "make_write_tool",
    "resolve_and_check",
    "safe_read_for_write",
    "safe_write",
    "validate_args",
    "wrap_tool_with_pipeline",
    "wrap_tools_for_session",
]
