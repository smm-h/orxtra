"""Test handler functions for ScriptAction tests.

This module exists as a standalone importable so that importlib.import_module
can find it from the test directory (via conftest sys.path adjustment).
Using a separate module avoids the dual-import problem where pytest and
importlib load the same file under different module names.
"""

from __future__ import annotations

# Shared mutable state that tests can inspect.
script_calls: list[list[dict[str, object]]] = []
flush_calls: list[list[dict[str, object]]] = []


def sample_sync_handler(events: list[dict[str, object]]) -> None:
    script_calls.append(events)


async def sample_async_handler(events: list[dict[str, object]]) -> None:
    script_calls.append(events)


def flush_handler(events: list[dict[str, object]]) -> None:
    flush_calls.append(events)


async def async_flush_handler(events: list[dict[str, object]]) -> None:
    flush_calls.append(events)
