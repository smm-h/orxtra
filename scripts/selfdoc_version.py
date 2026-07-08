"""Selfdoc custom directive: resolve project version from rlsbl releasable."""

from pathlib import Path


def resolve(_attrs: dict, _config: dict, _body: str) -> str:
    version_file = Path(__file__).resolve().parent.parent / ".rlsbl-monorepo" / "releasables" / "orxtra" / "version"
    if version_file.is_file():
        return version_file.read_text().strip()
    return "unknown"
