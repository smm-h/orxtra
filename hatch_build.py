"""Custom hatch build hook for the orxtra aggregation wheel.

Standard (release) builds derive force_include from workspace members,
so the wheel contains all sub-packages without hand-maintaining a list.

Editable builds inject nothing: with bypass_selection and no force_include,
hatchling materializes zero snapshot files; sub-projects are resolved via
their individual .pth files, so source edits take effect immediately.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def _workspace_members(root: Path) -> list[str]:
    """Parse [tool.uv.workspace].members from pyproject.toml.

    Returns the raw member directory names (e.g. "write-safety").
    Uses a minimal parser to avoid a TOML dependency at build time
    (hatchling ships tomli only on Python < 3.11; we need 3.12+).
    """
    # Python 3.11+ has tomllib in stdlib
    import tomllib

    pyproject = root / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    members: list[str] = data["tool"]["uv"]["workspace"]["members"]
    return members


def _derive_force_include(root: Path) -> dict[str, str]:
    """Build the force_include mapping by discovering sub-package dirs.

    For each workspace member directory, finds the package directory
    under <member>/src/orxtra/<pkg>/ and maps it into orxtra/<pkg>.
    """
    mapping: dict[str, str] = {}
    for member in _workspace_members(root):
        member_path = root / member / "src" / "orxtra"
        if not member_path.is_dir():
            msg = f"Member {member!r} has no src/orxtra/ directory"
            raise RuntimeError(msg)

        # Each member contributes exactly one package under orxtra/
        pkg_dirs = [
            d
            for d in member_path.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        ]
        if len(pkg_dirs) != 1:
            msg = (
                f"Member {member!r} has {len(pkg_dirs)} package dirs "
                f"under src/orxtra/, expected exactly 1"
            )
            raise RuntimeError(msg)

        pkg_dir = pkg_dirs[0]
        source = os.path.join(member, "src", "orxtra", pkg_dir.name)
        target = os.path.join("orxtra", pkg_dir.name)
        mapping[source] = target

    return mapping


class CustomBuildHook(BuildHookInterface):
    """Inject force_include only for standard (non-editable) wheel builds."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if version == "standard":
            root = Path(self.root)
            force_include = _derive_force_include(root)
            build_data["force_include"].update(force_include)
        # For "editable" version: inject nothing. bypass_selection in
        # pyproject.toml prevents hatchling from needing file discovery,
        # and no force_include means zero materialized snapshot files.
