#!/usr/bin/env python3
"""Check that declared internal dependencies match actual runtime imports.

For each monorepo member, compares:
- Declared internal deps (orxtra-* in pyproject.toml [project].dependencies)
- Actual runtime imports of orxtra.* in src/ (excluding TYPE_CHECKING blocks)

Reports:
- UNDECLARED: imported at runtime but not in [project].dependencies
- UNUSED: declared in [project].dependencies but never imported at runtime

UNUSED entries may be intentional (e.g., deferred removals, transitive
guarantees). UNDECLARED entries are always errors -- the package will fail
to install standalone.

Also verifies that pyproject deps, [tool.uv.sources], and workspace.toml
depends_on all agree for every member.
"""

import ast
import sys
import tomllib
from pathlib import Path

_MIN_MODULE_PARTS = 2


def _is_type_checking_block(node: ast.AST) -> bool:
    """Return True if the node is a TYPE_CHECKING guard."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


def _collect_runtime_imports(tree: ast.Module) -> set[str]:
    """Collect orxtra sub-module names imported at runtime."""
    imports: set[str] = set()

    def _walk(body: list[ast.stmt], *, in_tc: bool = False) -> None:
        for node in body:
            if isinstance(node, ast.If) and _is_type_checking_block(node):
                _walk(node.body, in_tc=True)
                _walk(node.orelse, in_tc=True)
                continue

            if isinstance(node, (ast.Import, ast.ImportFrom)) and not in_tc:
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("orxtra."):
                    parts = node.module.split(".")
                    if len(parts) >= _MIN_MODULE_PARTS:
                        imports.add(parts[1])
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("orxtra."):
                            parts = alias.name.split(".")
                            if len(parts) >= _MIN_MODULE_PARTS:
                                imports.add(parts[1])

            # Recurse into nested blocks
            if isinstance(node, ast.If):
                _walk(node.body, in_tc=in_tc)
                _walk(node.orelse, in_tc=in_tc)
            elif hasattr(node, "body") and isinstance(node.body, list):
                _walk(node.body, in_tc=in_tc)
                if hasattr(node, "orelse") and isinstance(node.orelse, list):
                    _walk(node.orelse, in_tc=in_tc)
                if hasattr(node, "finalbody") and isinstance(node.finalbody, list):
                    _walk(node.finalbody, in_tc=in_tc)
                if hasattr(node, "handlers") and isinstance(node.handlers, list):
                    for handler in node.handlers:
                        if hasattr(handler, "body") and isinstance(handler.body, list):
                            _walk(handler.body, in_tc=in_tc)

    _walk(tree.body)
    return imports


def _dep_name_to_pkg(dep: str) -> str:
    """Convert dependency name to Python package name (orxtra-foo -> foo)."""
    return dep.removeprefix("orxtra-").replace("-", "_")


def _load_workspace(repo_root: Path) -> dict[str, list[str]]:
    """Load workspace.toml and return member -> depends_on mapping."""
    ws_path = repo_root / ".rlsbl-monorepo" / "workspace.toml"
    with ws_path.open("rb") as f:
        ws = tomllib.load(f)
    result: dict[str, list[str]] = {}
    for project in ws["projects"]:
        path = project["path"]
        if path == ".":
            continue
        result[path] = sorted(project.get("depends_on", []))
    return result


def _check_member(
    repo_root: Path,
    member: str,
    ws_depends_on: list[str],
) -> tuple[list[str], list[str]]:
    """Check a single member. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    pyproject_path = repo_root / member / "pyproject.toml"
    if not pyproject_path.exists():
        return errors, warnings

    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    # Extract declared internal deps
    deps = data.get("project", {}).get("dependencies", [])
    declared_deps = sorted(
        _dep_name_to_pkg(d) for d in deps if d.startswith("orxtra-")
    )

    # Extract uv.sources
    sources = sorted(
        _dep_name_to_pkg(s)
        for s in data.get("tool", {}).get("uv", {}).get("sources", {})
    )

    # Normalize workspace.toml depends_on (hyphens to underscores)
    ws_normalized = sorted(d.replace("-", "_") for d in ws_depends_on)

    # Reconciliation check: all three sites must agree
    if declared_deps != sources:
        errors.append(
            f"{member}: pyproject deps {declared_deps} != "
            f"uv.sources {sources}"
        )
    if declared_deps != ws_normalized:
        errors.append(
            f"{member}: pyproject deps {declared_deps} != "
            f"workspace.toml depends_on {ws_depends_on}"
        )

    # Collect actual runtime imports from src/
    pkg_name = member.replace("-", "_")
    src_dir = repo_root / member / "src" / "orxtra" / pkg_name
    if not src_dir.exists():
        return errors, warnings

    actual_imports: set[str] = set()
    for py_file in src_dir.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            actual_imports |= _collect_runtime_imports(tree)
        except SyntaxError as e:
            warnings.append(f"{member}: parse error in {py_file}: {e}")

    # Self-imports are not dependencies
    actual_imports.discard(pkg_name)

    declared_set = set(declared_deps)

    undeclared = sorted(actual_imports - declared_set)
    unused = sorted(declared_set - actual_imports)

    for imp in undeclared:
        errors.append(
            f"{member}: UNDECLARED -- imports orxtra.{imp} at runtime "
            f"but orxtra-{imp.replace('_', '-')} is not in "
            f"[project].dependencies"
        )

    for dep in unused:
        warnings.append(
            f"{member}: UNUSED -- orxtra-{dep.replace('_', '-')} is in "
            f"[project].dependencies but orxtra.{dep} is never imported "
            f"at runtime in src/"
        )

    return errors, warnings


def main() -> int:
    repo_root = Path()
    ws_members = _load_workspace(repo_root)

    all_errors: list[str] = []
    all_warnings: list[str] = []

    for member in sorted(ws_members):
        ws_deps = ws_members[member]
        member_errors, member_warnings = _check_member(
            repo_root, member, ws_deps,
        )
        all_errors.extend(member_errors)
        all_warnings.extend(member_warnings)

    for e in all_errors:
        print(f"ERROR: {e}")
    for w in all_warnings:
        print(f"WARNING: {w}")

    if all_errors:
        print(f"\n{len(all_errors)} error(s), {len(all_warnings)} warning(s)")
        return 1
    if all_warnings:
        print(f"\n0 errors, {len(all_warnings)} warning(s)")
    else:
        print("All dependency declarations match runtime imports")
    return 0


if __name__ == "__main__":
    sys.exit(main())
