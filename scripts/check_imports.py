#!/usr/bin/env python3
"""Check that orxtra sub-project imports respect layer boundaries.

Walks all .py files under */src/orxtra/*/ and */tests/ and verifies:
1. No source module imports from a higher layer at runtime
   (TYPE_CHECKING imports are OK).
2. No cross-package private imports (from orxtra.X._foo where X is a
   different package). Source-file violations are errors; test-file
   violations are warnings.
"""

import ast
import sys
from pathlib import Path

LAYERS = {
    "foundation": {
        "protocols",
        "secrets",
        "write_safety",
        "transport",
        "agent",
        "tool",
        "verify",
        "trace",
        "notepad",
        "session",
    },
    "orchestration": {"scheduler", "dispatch"},
    "intelligence": {"overseer"},
    "composition": {"services"},
    "interfaces": {"cli", "mcp"},
}

# Build reverse lookup: module_name -> layer_name
MODULE_TO_LAYER: dict[str, str] = {}
for layer_name, modules in LAYERS.items():
    for mod in modules:
        MODULE_TO_LAYER[mod] = layer_name

# Layer ordering: lower index = lower layer.
# Orchestration and intelligence are at the same level but cannot
# cross-import each other.
LAYER_ORDER = {
    "foundation": 0,
    "orchestration": 1,
    "intelligence": 1,
    "composition": 2,
    "interfaces": 3,
}


def _is_type_checking_block(node: ast.AST) -> bool:
    """Return True if the node is a TYPE_CHECKING block.

    Matches both `if TYPE_CHECKING:` and `if typing.TYPE_CHECKING:`.
    """
    if not isinstance(node, ast.If):
        return False
    test = node.test
    # Plain `TYPE_CHECKING`
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    # `typing.TYPE_CHECKING`
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


_MIN_MODULE_PARTS = 2
_PRIVATE_IMPORT_MIN_PARTS = 3


def _extract_target_module(node: ast.Import | ast.ImportFrom) -> str | None:
    """Extract the orxtra sub-module name from an import node.

    Returns None if not an orxtra import.
    """
    if isinstance(node, ast.ImportFrom):
        if node.module and node.module.startswith("orxtra."):
            parts = node.module.split(".")
            if len(parts) >= _MIN_MODULE_PARTS:
                return parts[1]
    elif isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("orxtra."):
                parts = alias.name.split(".")
                if len(parts) >= _MIN_MODULE_PARTS:
                    return parts[1]
    return None


def _collect_runtime_imports(  # noqa: C901
    tree: ast.Module,
) -> list[tuple[int, str]]:
    """Collect all runtime orxtra imports as (line_number, target_module) pairs.

    Skips imports inside TYPE_CHECKING blocks.
    """
    results: list[tuple[int, str]] = []

    def _walk_body(body: list[ast.stmt], in_type_checking: bool = False) -> None:  # noqa: C901
        for node in body:
            if isinstance(node, ast.If) and _is_type_checking_block(node):
                # Recurse into the if-body marking it as type-checking
                _walk_body(node.body, in_type_checking=True)
                _walk_body(node.orelse, in_type_checking=True)
                continue

            if isinstance(node, (ast.Import, ast.ImportFrom)) and not in_type_checking:
                target = _extract_target_module(node)
                if target is not None:
                    results.append((node.lineno, target))

            # Recurse into nested blocks (classes, functions, other ifs, etc.)
            if isinstance(node, ast.If):
                _walk_body(node.body, in_type_checking)
                _walk_body(node.orelse, in_type_checking)
            elif hasattr(node, "body") and isinstance(node.body, list):
                _walk_body(node.body, in_type_checking)
                if hasattr(node, "orelse") and isinstance(node.orelse, list):
                    _walk_body(node.orelse, in_type_checking)
                if hasattr(node, "finalbody") and isinstance(node.finalbody, list):
                    _walk_body(node.finalbody, in_type_checking)
                if hasattr(node, "handlers") and isinstance(node.handlers, list):
                    for handler in node.handlers:
                        if hasattr(handler, "body") and isinstance(handler.body, list):
                            _walk_body(handler.body, in_type_checking)

    _walk_body(tree.body)
    return results


def _is_import_allowed(source_layer: str, target_layer: str) -> bool:
    """Check whether source_layer is allowed to import from target_layer."""
    if source_layer == target_layer:
        return True
    source_order = LAYER_ORDER[source_layer]
    target_order = LAYER_ORDER[target_layer]
    # Can only import from strictly lower layers.
    # Same level but different layer (orchestration <-> intelligence): forbidden.
    return target_order < source_order


def _collect_cross_package_private_imports(
    tree: ast.Module,
    source_package: str,
) -> list[tuple[int, str, str]]:
    """Collect runtime imports of private modules from other orxtra packages.

    For each ``from orxtra.X._foo import ...`` where X != source_package,
    returns (line_number, target_package, full_module_path).
    Skips imports inside TYPE_CHECKING blocks.
    """
    results: list[tuple[int, str, str]] = []

    def _walk_body(body: list[ast.stmt], in_type_checking: bool = False) -> None:
        for node in body:
            if isinstance(node, ast.If) and _is_type_checking_block(node):
                _walk_body(node.body, in_type_checking=True)
                _walk_body(node.orelse, in_type_checking=True)
                continue

            if isinstance(node, ast.ImportFrom) and not in_type_checking:
                if node.module is not None:
                    parts = node.module.split(".")
                    # Match orxtra.X._something
                    if (
                        len(parts) >= _PRIVATE_IMPORT_MIN_PARTS
                        and parts[0] == "orxtra"
                        and parts[2].startswith("_")
                        and parts[1] != source_package
                    ):
                        results.append((node.lineno, parts[1], node.module))

            if isinstance(node, ast.Import) and not in_type_checking:
                for alias in node.names:
                    parts = alias.name.split(".")
                    if (
                        len(parts) >= _PRIVATE_IMPORT_MIN_PARTS
                        and parts[0] == "orxtra"
                        and parts[2].startswith("_")
                        and parts[1] != source_package
                    ):
                        results.append((node.lineno, parts[1], alias.name))

            # Recurse into nested blocks
            if isinstance(node, ast.If):
                _walk_body(node.body, in_type_checking)
                _walk_body(node.orelse, in_type_checking)
            elif hasattr(node, "body") and isinstance(node.body, list):
                _walk_body(node.body, in_type_checking)
                if hasattr(node, "orelse") and isinstance(node.orelse, list):
                    _walk_body(node.orelse, in_type_checking)
                if hasattr(node, "finalbody") and isinstance(node.finalbody, list):
                    _walk_body(node.finalbody, in_type_checking)
                if hasattr(node, "handlers") and isinstance(node.handlers, list):
                    for handler in node.handlers:
                        if hasattr(handler, "body") and isinstance(handler.body, list):
                            _walk_body(handler.body, in_type_checking)

    _walk_body(tree.body)
    return results


def _module_name_from_path(py_file: Path) -> str | None:
    """Extract the orxtra sub-module name from a file path.

    E.g. .../scheduler/src/orxtra/scheduler/_executor.py -> 'scheduler'
         .../write-safety/src/orxtra/write_safety/queue.py -> 'write_safety'
    """
    parts = py_file.parts
    try:
        # Find 'orxtra' in the path after 'src'
        for i, part in enumerate(parts):
            if (
                part == "src"
                and i + 1 < len(parts)
                and parts[i + 1] == "orxtra"
                and i + 2 < len(parts)
            ):
                return parts[i + 2]
    except (IndexError, ValueError):
        pass
    return None


def _module_name_from_test_path(py_file: Path) -> str | None:
    """Extract the orxtra sub-module name from a test file path.

    E.g. .../scheduler/tests/test_foo.py -> 'scheduler'
         .../write-safety/tests/test_bar.py -> 'write_safety'

    Returns None for root-level tests/ (not under a sub-project).
    """
    parts = py_file.parts
    for i, part in enumerate(parts):
        if part == "tests" and i > 0:
            # The directory before 'tests' is the sub-project directory.
            # Convert directory name to Python package name (e.g.
            # write-safety -> write_safety).
            subproject_dir = parts[i - 1]
            pkg_name = subproject_dir.replace("-", "_")
            if pkg_name in MODULE_TO_LAYER:
                return pkg_name
    return None


def _check_file(  # noqa: C901
    py_file: Path,
    source_module: str,
    *,
    is_test: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Run all import checks on a single file.

    Appends to errors (src files) or warnings (test files).
    """
    source_layer = MODULE_TO_LAYER.get(source_module)
    # For test files, source_layer may be None (e.g. root-level tests
    # with source_module="__root__") -- that's fine, we still check
    # cross-package private imports. For src files, unknown layers are
    # skipped.
    if source_layer is None and not is_test:
        return

    try:
        source_text = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source_text, filename=str(py_file))
    except SyntaxError as e:
        print(f"WARNING: Failed to parse {py_file}: {e}", file=sys.stderr)
        return

    # Layer violation check (src files only -- tests are expected to
    # import from any layer)
    if not is_test:
        runtime_imports = _collect_runtime_imports(tree)
        for lineno, target_module in runtime_imports:
            if target_module == source_module:
                continue
            target_layer = MODULE_TO_LAYER.get(target_module)
            if target_layer is None:
                continue
            if not _is_import_allowed(source_layer, target_layer):
                msg = (
                    f"VIOLATION: {py_file}:{lineno} - "
                    f"{source_module} ({source_layer}) imports "
                    f"{target_module} ({target_layer})"
                )
                errors.append(msg)

    # Cross-package private import check
    private_imports = _collect_cross_package_private_imports(
        tree, source_module,
    )
    for lineno, target_pkg, full_path in private_imports:
        severity = "WARNING" if is_test else "VIOLATION"
        target = errors if not is_test else warnings
        msg = (
            f"{severity}: {py_file}:{lineno} - "
            f"{source_module} imports private module "
            f"'{full_path}' from package {target_pkg} "
            f"(use orxtra.{target_pkg} public API instead)"
        )
        target.append(msg)


def _dedup_paths(paths: list[Path]) -> list[Path]:
    """Deduplicate paths while preserving order."""
    seen: set[Path] = set()
    result: list[Path] = []
    for p in paths:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(p)
    return result


def main() -> int:
    repo_root = Path()
    errors: list[str] = []
    warnings: list[str] = []

    # --- Source files: */src/orxtra/*/ ---
    src_files = sorted(repo_root.glob("*/src/orxtra/*/**/*.py"))
    src_files += sorted(repo_root.glob("*/src/orxtra/*/*.py"))
    src_files = _dedup_paths(src_files)

    for py_file in src_files:
        source_module = _module_name_from_path(py_file)
        if source_module is None:
            continue
        _check_file(
            py_file, source_module,
            is_test=False, errors=errors, warnings=warnings,
        )

    # --- Test files: */tests/**/*.py ---
    test_files = sorted(repo_root.glob("*/tests/**/*.py"))
    test_files += sorted(repo_root.glob("*/tests/*.py"))
    # Root-level tests/ (not under a sub-project) -- skip these since
    # they have no single owning package
    root_test_files = sorted(repo_root.glob("tests/**/*.py"))
    root_test_files += sorted(repo_root.glob("tests/*.py"))
    test_files += root_test_files
    test_files = _dedup_paths(test_files)

    for py_file in test_files:
        source_module = _module_name_from_test_path(py_file)
        if source_module is None:
            # Root-level tests -- no owning package, so every
            # cross-package private import is a warning
            # Use a sentinel to flag all private imports
            source_module = "__root__"
        _check_file(
            py_file, source_module,
            is_test=True, errors=errors, warnings=warnings,
        )

    for e in errors:
        print(e)
    for w in warnings:
        print(w)

    total = len(errors) + len(warnings)
    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    if warnings:
        print(f"\n{len(warnings)} warning(s), 0 errors")
    else:
        print("No violations found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
