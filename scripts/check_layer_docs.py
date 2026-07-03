#!/usr/bin/env python3
"""Check that the architecture layer table in docs/_CLAUDE.md matches workspace.toml.

Parses the Markdown table from the template and compares it against the
authoritative [layers.assignments] in .rlsbl-monorepo/workspace.toml.
Hard-errors with a diff on any mismatch.
"""

import re
import sys
import tomllib
from pathlib import Path


def _parse_workspace_layers(workspace_path: Path) -> dict[str, set[str]]:
    """Load layer assignments from workspace.toml.

    Returns a dict mapping layer name -> set of project directory names
    (as they appear in workspace.toml, with hyphens preserved).
    """
    with workspace_path.open("rb") as f:
        data = tomllib.load(f)
    assignments: dict[str, list[str]] = data["layers"]["assignments"]
    return {layer: set(projects) for layer, projects in assignments.items()}


def _parse_template_layers(template_path: Path) -> dict[str, set[str]]:
    """Parse the architecture layer table from docs/_CLAUDE.md.

    Expects a Markdown table with columns: Layer | Sub-projects | Dependencies
    Extracts project names from Markdown links like [name](path/).

    Returns a dict mapping lowercase layer name -> set of project directory
    names (extracted from the link paths, trailing slash stripped).
    """
    text = template_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find the table. Look for the header row containing "Layer" and
    # "Sub-projects", then skip the separator row.
    table_start = None
    for i, line in enumerate(lines):
        if "Layer" in line and "Sub-projects" in line and "|" in line:
            table_start = i
            break

    if table_start is None:
        print("ERROR: Could not find architecture layer table in template", file=sys.stderr)
        sys.exit(1)

    # Skip header and separator rows.
    data_start = table_start + 2

    # Link pattern: [display-name](path/) -- we extract the path
    # (stripping trailing slash).
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+?)/?\)")

    result: dict[str, set[str]] = {}
    for line in lines[data_start:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.split("|")]
        # Split by | gives empty strings at start/end for well-formed
        # table rows. Filter them.
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        layer_name = cells[0].strip().lower()
        sub_projects_cell = cells[1]
        # Extract project names from links.
        projects: set[str] = set()
        for match in link_pattern.finditer(sub_projects_cell):
            path = match.group(2).rstrip("/")
            projects.add(path)
        if projects:
            result[layer_name] = projects

    return result


def main() -> int:
    repo_root = Path()
    workspace_path = repo_root / ".rlsbl-monorepo" / "workspace.toml"
    template_path = repo_root / "docs" / "_CLAUDE.md"

    if not workspace_path.is_file():
        print(f"ERROR: {workspace_path} not found", file=sys.stderr)
        return 1
    if not template_path.is_file():
        print(f"ERROR: {template_path} not found", file=sys.stderr)
        return 1

    workspace_layers = _parse_workspace_layers(workspace_path)
    template_layers = _parse_template_layers(template_path)

    errors: list[str] = []

    # Compare layer names.
    ws_layer_names = set(workspace_layers.keys())
    tpl_layer_names = set(template_layers.keys())

    missing_layers = ws_layer_names - tpl_layer_names
    extra_layers = tpl_layer_names - ws_layer_names

    for layer in sorted(missing_layers):
        errors.append(
            f"Layer '{layer}' exists in workspace.toml but is missing from docs/_CLAUDE.md"
        )
    for layer in sorted(extra_layers):
        errors.append(
            f"Layer '{layer}' exists in docs/_CLAUDE.md but is missing from workspace.toml"
        )

    # Compare project assignments within shared layers.
    for layer in sorted(ws_layer_names & tpl_layer_names):
        ws_projects = workspace_layers[layer]
        tpl_projects = template_layers[layer]

        missing_projects = ws_projects - tpl_projects
        extra_projects = tpl_projects - ws_projects

        for proj in sorted(missing_projects):
            errors.append(
                f"Layer '{layer}': project '{proj}' is in workspace.toml "
                f"but missing from docs/_CLAUDE.md"
            )
        for proj in sorted(extra_projects):
            errors.append(
                f"Layer '{layer}': project '{proj}' is in docs/_CLAUDE.md "
                f"but missing from workspace.toml"
            )

    if errors:
        print("Layer documentation is out of sync with workspace.toml:\n")
        for error in errors:
            print(f"  {error}")
        print(f"\n{len(errors)} error(s) found")
        return 1

    print("Layer documentation matches workspace.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
