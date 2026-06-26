"""Selfdoc custom directive: count non-dev_node workspace projects."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def resolve(attrs: dict, config: dict, body: str) -> str:
    workspace_file = Path(__file__).resolve().parent.parent / ".rlsbl-monorepo" / "workspace.toml"
    if not workspace_file.is_file():
        return "unknown"
    data = tomllib.loads(workspace_file.read_text())
    projects = data.get("projects", [])
    count = sum(1 for p in projects if not p.get("dev_node", False))
    return str(count)
