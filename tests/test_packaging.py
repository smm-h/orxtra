"""CI conformance tests for the build/packaging system.

These tests verify that the derived meta-build stays in sync:
(a) dev dependency group == workspace members
(b) built wheel contains all workspace member sub-packages
(c) imports resolve to source trees, not site-packages snapshots
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_pyproject() -> dict[str, Any]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def _workspace_members() -> list[str]:
    """Return raw workspace member directory names."""
    data = _load_pyproject()
    return data["tool"]["uv"]["workspace"]["members"]


def _dev_group_member_packages() -> set[str]:
    """Return orxtra-* package names in the dev dependency group."""
    data = _load_pyproject()
    dev_deps: list[str] = data["dependency-groups"]["dev"]
    return {d for d in dev_deps if d.startswith("orxtra-")}


def _member_to_package_name(member: str) -> str:
    """Convert workspace member dir name to package name."""
    return f"orxtra-{member}"


def _member_to_orxtra_subpackage(member: str) -> str:
    """Discover the orxtra sub-package name for a workspace member."""
    orxtra_dir = REPO_ROOT / member / "src" / "orxtra"
    pkg_dirs = [
        d.name
        for d in orxtra_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    ]
    assert len(pkg_dirs) == 1, (
        f"Expected 1 package dir under {orxtra_dir}, "
        f"got {pkg_dirs}"
    )
    return pkg_dirs[0]


def _wheel_subpackages(wheel: Path) -> set[str]:
    """Extract orxtra sub-package names from a wheel file."""
    subpackages: set[str] = set()
    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            parts = name.split("/")
            if (
                len(parts) >= 3
                and parts[0] == "orxtra"
                and not parts[1].startswith("_")
            ):
                subpackages.add(parts[1])
    return subpackages


class TestDevGroupMembership:
    """Verify dev dependency group matches workspace members."""

    def test_all_members_in_dev_group(self) -> None:
        members = _workspace_members()
        dev_packages = _dev_group_member_packages()
        expected = {_member_to_package_name(m) for m in members}
        missing = expected - dev_packages
        assert not missing, (
            f"Workspace members missing from dev group: {missing}"
        )

    def test_no_extra_in_dev_group(self) -> None:
        members = _workspace_members()
        dev_packages = _dev_group_member_packages()
        expected = {_member_to_package_name(m) for m in members}
        extra = dev_packages - expected
        assert not extra, (
            f"Dev group has packages not in workspace: {extra}"
        )

    def test_exact_match(self) -> None:
        members = _workspace_members()
        dev_packages = _dev_group_member_packages()
        expected = {_member_to_package_name(m) for m in members}
        assert dev_packages == expected


@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a wheel once per test module for inspection."""
    build_dir = tmp_path_factory.mktemp("wheel-build")
    uv = shutil.which("uv")
    assert uv is not None, "uv not found on PATH"
    result = subprocess.run(  # noqa: S603
        [uv, "build", "--wheel", "--out-dir", str(build_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        f"uv build failed:\n{result.stderr}"
    )
    wheels = list(build_dir.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, got {wheels}"
    return wheels[0]


class TestWheelContents:
    """Verify built wheel contains all workspace member sub-packages."""

    def test_wheel_contains_all_members(
        self, wheel_path: Path,
    ) -> None:
        members = _workspace_members()
        expected = {
            _member_to_orxtra_subpackage(m) for m in members
        }
        actual = _wheel_subpackages(wheel_path)
        missing = expected - actual
        assert not missing, (
            f"Wheel missing sub-packages: {missing}"
        )

    def test_wheel_has_no_extra_subpackages(
        self, wheel_path: Path,
    ) -> None:
        members = _workspace_members()
        expected = {
            _member_to_orxtra_subpackage(m) for m in members
        }
        actual = _wheel_subpackages(wheel_path)
        extra = actual - expected
        assert not extra, (
            f"Wheel has unexpected sub-packages: {extra}"
        )


class TestImportResolution:
    """Verify imports resolve to source trees."""

    def test_protocols_resolves_to_src(self) -> None:
        import orxtra.protocols  # noqa: PLC0415

        init_path = Path(orxtra.protocols.__file__)
        expected = REPO_ROOT / "protocols" / "src"
        assert str(init_path).startswith(str(expected)), (
            f"orxtra.protocols resolved to {init_path}, "
            f"expected under {expected}"
        )

    def test_transport_resolves_to_src(self) -> None:
        import orxtra.transport  # noqa: PLC0415

        init_path = Path(orxtra.transport.__file__)
        expected = REPO_ROOT / "transport" / "src"
        assert str(init_path).startswith(str(expected)), (
            f"orxtra.transport resolved to {init_path}, "
            f"expected under {expected}"
        )

    def test_scheduler_resolves_to_src(self) -> None:
        import orxtra.scheduler  # noqa: PLC0415

        init_path = Path(orxtra.scheduler.__file__)
        expected = REPO_ROOT / "scheduler" / "src"
        assert str(init_path).startswith(str(expected)), (
            f"orxtra.scheduler resolved to {init_path}, "
            f"expected under {expected}"
        )

    def test_no_orxtra_dir_in_site_packages(self) -> None:
        """No materialized orxtra/ snapshot dir in site-packages."""
        site_packages = Path(sys.prefix) / "lib"
        python_dirs = [
            d
            for d in site_packages.iterdir()
            if d.name.startswith("python")
        ]
        for pydir in python_dirs:
            sp = pydir / "site-packages" / "orxtra"
            if sp.exists():
                pkg_dirs = [
                    d.name
                    for d in sp.iterdir()
                    if d.is_dir()
                    and not d.name.startswith("_")
                ]
                assert not pkg_dirs, (
                    f"Materialized snapshot dirs in {sp}: "
                    f"{pkg_dirs}. Editable install is creating "
                    f"stale copies."
                )
