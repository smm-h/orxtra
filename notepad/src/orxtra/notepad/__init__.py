from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-notepad")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.notepad._reader import format_notepad, read_notepad
from orxtra.notepad._types import NotepadEntry

__all__ = [
    "__version__",
    "NotepadEntry",
    "format_notepad",
    "read_notepad",
]
