#!/usr/bin/env python3
"""Check that orxt sub-project imports respect layer boundaries.

Walks all .py files under */src/orxt/*/ and verifies that no module
imports from a higher layer at runtime (TYPE_CHECKING imports are OK).
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
    "orchestration": {"scheduler"},
    "intelligence": {"overseer", "knowledge_module"},
    "interfaces": {"services", "cli", "mcp"},
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
    "interfaces": 2,
}


def _is_type_checking_block(node: ast.AST) -> bool:
    """Return True if the node is an `if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:` block."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    # Plain `TYPE_CHECKING`
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    # `typing.TYPE_CHECKING`
    if (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    ):
        return True
    return False


def _extract_target_module(node: ast.Import | ast.ImportFrom) -> str | None:
    """Extract the orxt sub-module name from an import node, or None if not an orxt import."""
    if isinstance(node, ast.ImportFrom):
        if node.module and node.module.startswith("orxt."):
            parts = node.module.split(".")
            if len(parts) >= 2:
                return parts[1]
    elif isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("orxt."):
                parts = alias.name.split(".")
                if len(parts) >= 2:
                    return parts[1]
    return None


def _collect_runtime_imports(
    tree: ast.Module,
) -> list[tuple[int, str]]:
    """Collect all runtime orxt imports as (line_number, target_module) pairs.

    Skips imports inside TYPE_CHECKING blocks.
    """
    results: list[tuple[int, str]] = []

    def _walk_body(body: list[ast.stmt], in_type_checking: bool = False) -> None:
        for node in body:
            if isinstance(node, ast.If) and _is_type_checking_block(node):
                # Recurse into the if-body marking it as type-checking
                _walk_body(node.body, in_type_checking=True)
                _walk_body(node.orelse, in_type_checking=True)
                continue

            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if not in_type_checking:
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
    # Can only import from strictly lower layers
    if target_order < source_order:
        return True
    # Same level but different layer (orchestration <-> intelligence): forbidden
    return False


def _module_name_from_path(py_file: Path) -> str | None:
    """Extract the orxt sub-module name from a file path.

    E.g. .../scheduler/src/orxt/scheduler/_executor.py -> 'scheduler'
         .../write-safety/src/orxt/write_safety/queue.py -> 'write_safety'
    """
    parts = py_file.parts
    try:
        # Find 'orxt' in the path after 'src'
        for i, part in enumerate(parts):
            if part == "src" and i + 1 < len(parts) and parts[i + 1] == "orxt":
                if i + 2 < len(parts):
                    return parts[i + 2]
    except (IndexError, ValueError):
        pass
    return None


def main() -> int:
    repo_root = Path(".")
    violations: list[str] = []

    # Find all .py files under */src/orxt/*/
    py_files = sorted(repo_root.glob("*/src/orxt/*/**/*.py"))
    # Also include files directly in */src/orxt/*/ (like __init__.py)
    py_files += sorted(repo_root.glob("*/src/orxt/*/*.py"))
    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique_files: list[Path] = []
    for f in py_files:
        resolved = f.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_files.append(f)
    py_files = unique_files

    for py_file in py_files:
        source_module = _module_name_from_path(py_file)
        if source_module is None:
            continue
        source_layer = MODULE_TO_LAYER.get(source_module)
        if source_layer is None:
            continue

        try:
            source_text = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(py_file))
        except SyntaxError as e:
            print(f"WARNING: Failed to parse {py_file}: {e}", file=sys.stderr)
            continue

        runtime_imports = _collect_runtime_imports(tree)
        for lineno, target_module in runtime_imports:
            # Self-imports are always fine
            if target_module == source_module:
                continue
            target_layer = MODULE_TO_LAYER.get(target_module)
            if target_layer is None:
                # Not an orxt module we track
                continue
            if not _is_import_allowed(source_layer, target_layer):
                msg = (
                    f"VIOLATION: {py_file}:{lineno} - "
                    f"{source_module} ({source_layer}) imports "
                    f"{target_module} ({target_layer})"
                )
                violations.append(msg)

    for v in violations:
        print(v)

    if violations:
        print(f"\n{len(violations)} violation(s) found")
        return 1
    else:
        print("No violations found")
        return 0


if __name__ == "__main__":
    sys.exit(main())
