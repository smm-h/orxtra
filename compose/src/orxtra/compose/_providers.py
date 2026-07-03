"""Built-in fragment providers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.compose._fragment import Fragment
from orxtra.compose._includes import resolve_includes

if TYPE_CHECKING:
    from pathlib import Path


class FileFragmentProvider:
    """Discovers .md files in a directory and provides them as fragments.

    Each .md file becomes a Fragment with:
    - name: the file stem (e.g. "header" from "header.md")
    - content: the file text with {include:...} directives resolved
    - priority: from the constructor (uniform for all files)
    - source: "file:<directory_path>"
    """

    def __init__(self, directory: Path, priority: int = 0) -> None:
        self._directory = directory
        self._priority = priority

    def fragments(self, context: dict[str, Any]) -> list[Fragment]:  # noqa: ARG002
        if not self._directory.is_dir():
            return []

        result: list[Fragment] = []
        for md_file in sorted(self._directory.glob("*.md")):
            raw = md_file.read_text()
            content = resolve_includes(raw, self._directory)
            result.append(
                Fragment(
                    name=md_file.stem,
                    content=content,
                    priority=self._priority,
                    source=f"file:{self._directory}",
                )
            )
        return result
