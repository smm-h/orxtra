"""Workspace-root pytest conftest.

pytest runs with --import-mode=importlib. Every sub-project has a
tests/ directory, and the repo root has a shared tests/ package.
Without intervention, root-level collection derives colliding module
names ("tests.*") for every sub-project's test files, or synthesizes
namespace-package parents (e.g. "a2a", "mcp") from workspace
directories, shadowing the real installed SDKs of the same name.

This conftest makes root-level collection deterministic:

1. Puts the repo root on sys.path so the shared root tests package
   (tests.pg_fixtures, tests.shared_mocks) is importable.
2. Imports the real a2a and mcp SDKs before pytest's import machinery
   can register the workspace a2a/ and mcp/ directories as namespace
   packages that would shadow them.
3. Pre-seeds sys.modules with a "<project>.tests" package entry for
   every sub-project, so pytest's importlib fallback naming imports
   each test module under a unique name ("<project>.tests.test_x")
   without synthesizing a "<project>" namespace parent. This also
   keeps stdlib modules that share a sub-project name (secrets, trace)
   from being replaced by namespace packages.

Sub-project tests/ directories intentionally have no __init__.py:
with one, pytest resolves every test module to the same "tests.*"
name and returns whichever module was imported first.
"""
from __future__ import annotations

import sys
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

if str(_ROOT) not in sys.path:
    sys.path.append(str(_ROOT))

# Import real SDKs whose top-level names collide with workspace
# directories, before pytest's import machinery can shadow them.
import a2a  # noqa: E402, F401
import mcp  # noqa: E402, F401

pytest_plugins = ["tests.pg_fixtures"]


def _seed_test_packages() -> None:
    for tests_dir in sorted(_ROOT.glob("*/tests")):
        if not tests_dir.is_dir():
            continue
        name = f"{tests_dir.parent.name}.tests"
        if name in sys.modules:
            continue
        spec = ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = [str(tests_dir)]
        sys.modules[name] = module_from_spec(spec)


_seed_test_packages()
