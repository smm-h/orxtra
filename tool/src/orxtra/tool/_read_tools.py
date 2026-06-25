"""File read tool constructors for the orxtra tool module."""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pathspec
from orxtra.protocols import (
    DiffResult,
    DirEntry,
    DirListing,
    FileContent,
    FileStat,
    GlobResult,
    GrepMatch,
    GrepResult,
    StatResult,
    Tool,
    ToolError,
    ToolOutput,
)
from orxtra.tool._decorator import tool
from orxtra.tool._params import (
    DiffParams,
    GlobParams,
    GrepParams,
    ListDirParams,
    ReadParams,
    StatParams,
)
from orxtra.tool._path import PathError, resolve_and_check
from orxtra.tool._preview import FullRetrievalGuard, check_and_preview
from orxtra.tool._renderers import TextRenderer


_BINARY_CHECK_SIZE = 8192

_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".lua": "lua",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".pl": "perl",
    ".pm": "perl",
    ".r": "r",
    ".R": "r",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".json": "json",
    ".jsonl": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".tex": "latex",
    ".php": "php",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".clj": "clojure",
    ".dart": "dart",
    ".zig": "zig",
    ".nim": "nim",
    ".v": "v",
    ".vue": "vue",
    ".svelte": "svelte",
    ".proto": "protobuf",
    ".tf": "terraform",
    ".dockerfile": "dockerfile",
    ".graphql": "graphql",
    ".gql": "graphql",
}


def _extension_to_language(ext: str) -> str | None:
    """Map a file extension to a language name, or None if unknown."""
    return _EXTENSION_LANGUAGES.get(ext)


def _is_binary(path: Path) -> bool:
    """Check if a file is binary by reading the first chunk.

    Tries to decode as UTF-8. If that fails or null bytes are present,
    the file is considered binary.
    """
    try:
        with path.open("rb") as f:
            chunk = f.read(_BINARY_CHECK_SIZE)
    except OSError:
        return False
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _path_error_to_tool_error(exc: PathError) -> ToolError:
    """Convert a PathError to a ToolError."""
    return ToolError(str(exc))


def _format_with_line_numbers(lines: list[str], start_lineno: int) -> str:
    """Format lines with right-aligned cat -n style line numbers.

    Args:
        lines: Lines of text (without trailing newlines from splitlines).
        start_lineno: The 1-based line number for the first line.

    Returns:
        Formatted string with ``{lineno}\\t{content}`` per line.
    """
    if not lines:
        return ""
    max_lineno = start_lineno + len(lines) - 1
    width = len(str(max_lineno))
    formatted = []
    for i, line in enumerate(lines):
        lineno = start_lineno + i
        formatted.append(f"{lineno:>{width}}\t{line}")
    return "\n".join(formatted)


def _resolve_path(raw_path: str, root: Path) -> Path:
    """Resolve a path, converting PathError to ToolError."""
    try:
        return resolve_and_check(raw_path, root)
    except PathError as exc:
        raise _path_error_to_tool_error(exc) from exc


def _read_text_file(resolved: Path) -> str:
    """Read a text file, raising ToolError on failure."""
    try:
        return resolved.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Failed to read file: {exc}"
        raise ToolError(msg) from exc


def _require_file(resolved: Path, label: str) -> None:
    """Raise ToolError if the path is not an existing file."""
    if not resolved.is_file():
        msg = f"File not found: {label}"
        raise ToolError(msg)


def _require_text_file(resolved: Path, label: str) -> None:
    """Raise ToolError if the file is binary."""
    if _is_binary(resolved):
        msg = f"Binary file, cannot diff: {label}"
        raise ToolError(msg)


def _require_dir(resolved: Path, label: str) -> None:
    """Raise ToolError if the path is not a directory."""
    if not resolved.is_dir():
        msg = f"Not a directory: {label}"
        raise ToolError(msg)


def _resolve_root(root: Path) -> Path:
    """Resolve the root path.

    Extracted to a non-async helper so ASYNC240 does not fire.
    """
    return root.resolve()


def _glob_within_root(
    base: Path,
    pattern: str,
    root_resolved: Path,
) -> list[Path]:
    """Glob for pattern and filter results to stay within root."""
    results: list[Path] = []
    root_str = str(root_resolved)
    for match in sorted(base.glob(pattern)):
        try:
            match_resolved = match.resolve()
        except OSError:
            continue
        if match_resolved != root_resolved and not str(
            match_resolved,
        ).startswith(root_str + os.sep):
            continue
        results.append(match_resolved)
    return results


# ---------------------------------------------------------------------------
# Read tool
# ---------------------------------------------------------------------------


@tool(
    "read",
    "Read file contents with line numbers.",
    renderer=TextRenderer(),
    namespace="fs.read",
    tags=frozenset({"readonly"}),
)
async def _read_impl(
    params: ReadParams,
    *,
    read_root: Path,
    preview_threshold: int,
    preview_lines: int,
    session_id: str,
    guard: FullRetrievalGuard,
) -> ToolOutput[FileContent]:
    resolved = _resolve_path(params.path, read_root)
    _require_file(resolved, params.path)

    if _is_binary(resolved):
        text = "Binary file, cannot display"
        return ToolOutput(
            data=FileContent(content=text, is_preview=False, total_lines=0, total_bytes=0),
            text=text,
        )

    raw_text = _read_text_file(resolved)
    all_lines = raw_text.splitlines()
    total_bytes = len(raw_text.encode("utf-8"))
    total_lines = len(all_lines)
    offset = params.offset if params.offset is not None else 1
    limit = params.limit
    full = params.full if params.full is not None else False

    # offset is 1-based
    start_idx = min(offset - 1, len(all_lines))
    end_idx = start_idx + limit if limit is not None else len(all_lines)
    selected = all_lines[start_idx:end_idx]
    start_lineno = start_idx + 1

    content = "\n".join(selected)
    path_str = str(resolved)

    if full:
        if not guard.check_full_allowed(session_id, path_str):
            msg = (
                "Cannot retrieve full content: no preview was "
                "previously returned for this file. Read the file "
                "first without full=true."
            )
            return ToolOutput(
                data=FileContent(content=msg, is_preview=False, total_lines=total_lines, total_bytes=total_bytes),
                text=msg,
            )
        formatted = _format_with_line_numbers(selected, start_lineno)
        return ToolOutput(
            data=FileContent(content=content, is_preview=False, total_lines=total_lines, total_bytes=total_bytes),
            text=formatted,
        )

    result = check_and_preview(content, preview_threshold, preview_lines)
    if result.is_preview:
        guard.record_preview(session_id, path_str)
        return ToolOutput(
            data=FileContent(content=content, is_preview=True, total_lines=total_lines, total_bytes=total_bytes),
            text=result.content,
        )

    formatted = _format_with_line_numbers(selected, start_lineno)
    return ToolOutput(
        data=FileContent(content=content, is_preview=False, total_lines=total_lines, total_bytes=total_bytes),
        text=formatted,
    )


def make_read_tool(
    read_root: Path,
    preview_threshold: int,
    preview_lines: int,
    session_id: str = "default",
    previewer: Any | None = None,  # noqa: ANN401
) -> Tool:
    """Construct the read file tool.

    Args:
        read_root: Root directory for path containment.
        preview_threshold: Byte threshold above which content is previewed.
        preview_lines: Number of head/tail lines in a preview.
        session_id: Identifier for the session using this tool.
        previewer: Unused, reserved for future custom preview logic.
    """
    _ = previewer  # Reserved for future use
    guard = FullRetrievalGuard()
    return _read_impl.bind(
        read_root=read_root,
        preview_threshold=preview_threshold,
        preview_lines=preview_lines,
        session_id=session_id,
        guard=guard,
    )


# ---------------------------------------------------------------------------
# List dir tool
# ---------------------------------------------------------------------------


def _load_gitignore(read_root: Path) -> pathspec.PathSpec | None:  # type: ignore[type-arg]
    """Load .gitignore patterns from the read root directory."""
    gitignore_path = read_root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines)


def _list_recursive(  # noqa: C901
    resolved: Path,
    pattern: str | None,
    gitignore: pathspec.PathSpec | None = None,  # type: ignore[type-arg]
) -> list[tuple[str, str, str]]:
    """Walk directory recursively, collecting entries."""
    entries: list[tuple[str, str, str]] = []
    for dirpath_str, dirnames, filenames in os.walk(resolved):
        dirpath = resolved.__class__(dirpath_str)
        # Prune ignored directories from traversal
        if gitignore:
            surviving = []
            for d in dirnames:
                rel = str((dirpath / d).relative_to(resolved))
                if not gitignore.match_file(rel + "/"):
                    surviving.append(d)
            dirnames[:] = surviving
        for dirname in dirnames:
            if pattern and not fnmatch.fnmatch(dirname, pattern):
                continue
            full = dirpath / dirname
            rel = str(full.relative_to(resolved))
            entries.append(("dir", "-", rel))
        for filename in filenames:
            if pattern and not fnmatch.fnmatch(filename, pattern):
                continue
            full = dirpath / filename
            rel = str(full.relative_to(resolved))
            if gitignore and gitignore.match_file(rel):
                continue
            try:
                size = str(full.stat().st_size)
            except OSError:
                size = "?"
            entries.append(("file", size, rel))
    return entries


def _list_immediate(
    resolved: Path,
    pattern: str | None,
    gitignore: pathspec.PathSpec | None = None,  # type: ignore[type-arg]
) -> list[tuple[str, str, str]]:
    """List immediate children of a directory."""
    entries: list[tuple[str, str, str]] = []
    try:
        children = sorted(resolved.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        msg = f"Failed to list directory: {exc}"
        raise ToolError(msg) from exc
    for child in children:
        name = child.name
        if pattern and not fnmatch.fnmatch(name, pattern):
            continue
        if child.is_dir():
            if gitignore and gitignore.match_file(name + "/"):
                continue
            entries.append(("dir", "-", name))
        else:
            if gitignore and gitignore.match_file(name):
                continue
            try:
                size = str(child.stat().st_size)
            except OSError:
                size = "?"
            entries.append(("file", size, name))
    return entries


@tool(
    "list_dir",
    "List directory contents with type and size information.",
    renderer=TextRenderer(),
    namespace="fs.read",
    tags=frozenset({"readonly"}),
)
async def _list_dir_impl(
    params: ListDirParams,
    *,
    read_root: Path,
) -> ToolOutput[DirListing]:
    resolved = _resolve_path(params.path, read_root)
    _require_dir(resolved, params.path)

    recursive = params.recursive if params.recursive is not None else False
    pattern = params.pattern
    max_results = params.max_results if params.max_results is not None else 500

    gitignore = _load_gitignore(read_root)
    if recursive:
        raw_entries = _list_recursive(resolved, pattern, gitignore)
    else:
        raw_entries = _list_immediate(resolved, pattern, gitignore)

    raw_entries.sort(key=lambda e: e[2])

    truncated = len(raw_entries) > max_results
    raw_entries = raw_entries[:max_results]

    dir_entries = [
        DirEntry(
            type=etype,
            size=int(esize) if esize != "-" and esize != "?" else None,
            path=epath,
        )
        for etype, esize, epath in raw_entries
    ]

    lines = [f"{etype}\t{esize}\t{epath}" for etype, esize, epath in raw_entries]
    if truncated:
        lines.append(f"(truncated at {max_results} results)")

    return ToolOutput(
        data=DirListing(entries=dir_entries, truncated=truncated),
        text="\n".join(lines),
    )


def make_list_dir_tool(read_root: Path) -> Tool:
    """Construct the list directory tool.

    Args:
        read_root: Root directory for path containment.
    """
    return _list_dir_impl.bind(read_root=read_root)


# ---------------------------------------------------------------------------
# Glob tool
# ---------------------------------------------------------------------------


@tool(
    "glob",
    "Find files by glob pattern.",
    renderer=TextRenderer(),
    namespace="fs.read",
    tags=frozenset({"readonly"}),
)
async def _glob_impl(
    params: GlobParams,
    *,
    read_root: Path,
) -> ToolOutput[GlobResult]:
    base_path_str = params.path
    if base_path_str is not None:
        base = _resolve_path(base_path_str, read_root)
    else:
        base = read_root

    _require_dir(base, base_path_str or str(read_root))

    pattern = params.pattern
    max_results = params.max_results if params.max_results is not None else 200

    root_resolved = _resolve_root(read_root)
    matches = _glob_within_root(base, pattern, root_resolved)

    results: list[str] = []
    for match_resolved in matches:
        try:
            rel = str(match_resolved.relative_to(root_resolved))
        except ValueError:
            continue
        results.append(rel)

    truncated = len(results) > max_results
    results = results[:max_results]

    lines = results.copy()
    if truncated:
        lines.append(f"(truncated at {max_results} results)")

    if not lines:
        return ToolOutput(
            data=GlobResult(paths=[], truncated=False),
            text="No matches found.",
        )

    return ToolOutput(
        data=GlobResult(paths=results, truncated=truncated),
        text="\n".join(lines),
    )


def make_glob_tool(read_root: Path) -> Tool:
    """Construct the glob tool.

    Args:
        read_root: Root directory for path containment.
    """
    return _glob_impl.bind(read_root=read_root)


# ---------------------------------------------------------------------------
# Grep tool
# ---------------------------------------------------------------------------


def _grep_compile(pattern: str, case_sensitive: bool) -> re.Pattern[str]:
    """Compile a regex pattern, raising ToolError on invalid input."""
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        msg = f"Invalid regex pattern: {exc}"
        raise ToolError(msg) from exc


def _grep_search_file(
    file_lines: list[str],
    rel_path: str,
    compiled: re.Pattern[str],
    context_lines_count: int,
    mode: str,
) -> tuple[list[str], int, bool]:
    """Search a single file for grep matches.

    Returns:
        Tuple of (output_lines, match_count, file_has_match).
    """
    output_lines: list[str] = []
    match_count = 0
    lines_to_show: set[int] = set()
    file_has_match = False

    for line_idx, line in enumerate(file_lines):
        if compiled.search(line):
            file_has_match = True
            match_count += 1

            if mode == "content":
                ctx_start = max(0, line_idx - context_lines_count)
                ctx_end = min(
                    len(file_lines),
                    line_idx + context_lines_count + 1,
                )
                for ctx_idx in range(ctx_start, ctx_end):
                    lines_to_show.add(ctx_idx)

    if file_has_match and mode == "content":
        for line_idx in sorted(lines_to_show):
            lineno = line_idx + 1
            output_lines.append(f"{rel_path}:{lineno}:{file_lines[line_idx]}")

    return output_lines, match_count, file_has_match


def _grep_iter_files(
    search_path: Path,
    read_root: Path,
    include: str | None,
) -> list[tuple[Path, str]]:
    """Walk directory tree and yield (filepath, rel_path) for searchable files.

    Skips hidden dirs/files, binary files, and applies the include filter.
    """
    results: list[tuple[Path, str]] = []
    for dirpath_str, dirnames, filenames in os.walk(search_path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            if include and not fnmatch.fnmatch(filename, include):
                continue
            filepath = search_path.__class__(dirpath_str) / filename
            if _is_binary(filepath):
                continue
            try:
                rel_path = str(filepath.relative_to(read_root))
            except ValueError:
                continue
            results.append((filepath, rel_path))
    return results


def _grep_format_results(  # noqa: PLR0913
    mode: str,
    output_lines: list[str],
    matched_files: list[str],
    total_match_count: int,
    max_results: int,
    hit_limit: bool,
    preview_threshold: int,
    preview_lines_count: int,
) -> str:
    """Format grep results based on mode."""
    if mode == "count":
        return str(total_match_count)

    if mode == "files_only":
        if not matched_files:
            return "No matches found."
        lines_out = matched_files.copy()
        if hit_limit:
            lines_out.append(f"(truncated at {max_results} results)")
        return "\n".join(lines_out)

    if not output_lines:
        return "No matches found."

    content = "\n".join(output_lines)
    if hit_limit:
        content += f"\n(truncated at {max_results} results)"

    result = check_and_preview(content, preview_threshold, preview_lines_count)
    return result.content


@tool(
    "grep",
    "Search file contents by regex pattern.",
    renderer=TextRenderer(),
    namespace="fs.read",
    tags=frozenset({"readonly"}),
)
async def _grep_impl(
    params: GrepParams,
    *,
    read_root: Path,
    preview_threshold: int,
    preview_lines: int,
) -> ToolOutput[GrepResult]:
    case_sensitive = params.case_sensitive if params.case_sensitive is not None else True
    compiled = _grep_compile(params.pattern, case_sensitive)

    search_path_str = params.path
    if search_path_str is not None:
        search_path = _resolve_path(search_path_str, read_root)
    else:
        search_path = read_root
    _require_dir(search_path, search_path_str or str(read_root))

    context_lines_count = params.context_lines if params.context_lines is not None else 0
    max_results = params.max_results if params.max_results is not None else 100
    include = params.include
    mode = params.mode if params.mode is not None else "content"

    output_lines: list[str] = []
    total_match_count = 0
    matched_files: list[str] = []
    grep_matches: list[GrepMatch] = []
    hit_limit = False

    files = _grep_iter_files(search_path, read_root, include)
    for filepath, rel_path in files:
        try:
            file_text = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        file_out, file_count, has_match = _grep_search_file(
            file_text.splitlines(),
            rel_path,
            compiled,
            context_lines_count,
            mode,
        )
        total_match_count += file_count

        if not has_match:
            continue

        if mode == "files_only":
            matched_files.append(rel_path)
            if len(matched_files) >= max_results:
                hit_limit = True
                break
        elif mode == "content":
            for line_str in file_out:
                parts = line_str.split(":", 2)
                if len(parts) >= 3:  # noqa: PLR2004
                    grep_matches.append(GrepMatch(
                        file=parts[0], line_number=int(parts[1]), line=parts[2],
                    ))
            output_lines.extend(file_out)
            if len(output_lines) >= max_results:
                hit_limit = True
                output_lines = output_lines[:max_results]
                break

    text = _grep_format_results(
        mode,
        output_lines,
        matched_files,
        total_match_count,
        max_results,
        hit_limit,
        preview_threshold,
        preview_lines,
    )
    return ToolOutput(
        data=GrepResult(
            matches=grep_matches,
            mode=mode,
            count=total_match_count if mode == "count" else None,
        ),
        text=text,
    )


def make_grep_tool(
    read_root: Path,
    preview_threshold: int,
    preview_lines: int,
) -> Tool:
    """Construct the grep tool.

    Args:
        read_root: Root directory for path containment.
        preview_threshold: Byte threshold above which output is previewed.
        preview_lines: Number of head/tail lines in a preview.
    """
    return _grep_impl.bind(
        read_root=read_root,
        preview_threshold=preview_threshold,
        preview_lines=preview_lines,
    )


# ---------------------------------------------------------------------------
# Stat tool
# ---------------------------------------------------------------------------


def _stat_single(path: Path, root: Path) -> dict[str, Any]:
    """Collect stat information for a single path."""
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)

    null_result: dict[str, Any] = {
        "path": rel,
        "exists": False,
        "byte_size": None,
        "line_count": None,
        "language": None,
        "mtime": None,
        "binary": None,
    }

    if not path.exists():
        return null_result

    try:
        st = path.stat()
    except OSError:
        return null_result

    binary = _is_binary(path)
    line_count: int | None = None
    if not binary and path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
            line_count = len(text.splitlines())
        except (OSError, UnicodeDecodeError):
            pass

    ext = path.suffix.lower()
    language = _extension_to_language(ext)
    mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat()

    return {
        "path": rel,
        "exists": True,
        "byte_size": st.st_size,
        "line_count": line_count,
        "language": language,
        "mtime": mtime,
        "binary": binary,
    }


@tool(
    "stat",
    "Get file metadata.",
    renderer=TextRenderer(),
    namespace="fs.read",
    tags=frozenset({"readonly"}),
)
async def _stat_impl(
    params: StatParams,
    *,
    read_root: Path,
) -> ToolOutput[StatResult]:
    raw_path = params.path

    has_glob = any(c in raw_path for c in ("*", "?", "["))
    root_resolved = _resolve_root(read_root)

    if has_glob:
        base = _resolve_path(".", read_root)
        matches = _glob_within_root(base, raw_path, root_resolved)
        results = [_stat_single(m, root_resolved) for m in matches]
        stats = [
            FileStat(
                path=r["path"], exists=r["exists"], byte_size=r["byte_size"],
                line_count=r["line_count"], language=r["language"],
                mtime=r["mtime"], binary=r.get("binary", False),
            )
            for r in results
        ]
        return ToolOutput(data=StatResult(files=stats), text=json.dumps(results, indent=2))

    resolved = _resolve_path(raw_path, read_root)
    info = _stat_single(resolved, root_resolved)
    stat_data = FileStat(
        path=info["path"], exists=info["exists"], byte_size=info["byte_size"],
        line_count=info["line_count"], language=info["language"],
        mtime=info["mtime"], binary=info.get("binary", False),
    )
    return ToolOutput(data=StatResult(files=[stat_data]), text=json.dumps(info, indent=2))


def make_stat_tool(read_root: Path) -> Tool:
    """Construct the stat tool.

    Args:
        read_root: Root directory for path containment.
    """
    return _stat_impl.bind(read_root=read_root)


# ---------------------------------------------------------------------------
# Diff tool
# ---------------------------------------------------------------------------


@tool(
    "diff",
    "Show unified diff between two files.",
    renderer=TextRenderer(),
    namespace="fs.read",
    tags=frozenset({"readonly"}),
)
async def _diff_impl(
    params: DiffParams,
    *,
    read_root: Path,
) -> ToolOutput[DiffResult]:
    resolved_a = _resolve_path(params.path_a, read_root)
    resolved_b = _resolve_path(params.path_b, read_root)
    _require_file(resolved_a, params.path_a)
    _require_file(resolved_b, params.path_b)
    _require_text_file(resolved_a, params.path_a)
    _require_text_file(resolved_b, params.path_b)

    text_a = _read_text_file(resolved_a)
    text_b = _read_text_file(resolved_b)

    root_resolved = _resolve_root(read_root)
    try:
        label_a = str(resolved_a.relative_to(root_resolved))
    except ValueError:
        label_a = str(resolved_a)
    try:
        label_b = str(resolved_b.relative_to(root_resolved))
    except ValueError:
        label_b = str(resolved_b)

    diff = difflib.unified_diff(
        text_a.splitlines(keepends=True),
        text_b.splitlines(keepends=True),
        fromfile=label_a,
        tofile=label_b,
    )

    diff_text = "".join(diff)
    identical = not diff_text
    if identical:
        display_text = "Files are identical."
    else:
        display_text = diff_text

    return ToolOutput(
        data=DiffResult(diff=diff_text, identical=identical),
        text=display_text,
    )


def make_diff_tool(read_root: Path) -> Tool:
    """Construct the diff tool.

    Args:
        read_root: Root directory for path containment.
    """
    return _diff_impl.bind(read_root=read_root)
