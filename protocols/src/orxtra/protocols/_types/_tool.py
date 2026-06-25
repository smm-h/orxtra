from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")


@dataclass(frozen=True)
class ToolOutput(Generic[T]):
    """Generic wrapper for typed tool results.

    ``data`` holds the structured result; ``text`` holds the
    human/LLM-readable rendering.
    """

    data: T
    text: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[[dict[str, Any]], Awaitable[ToolOutput[Any]]]
    suspending: bool = False
    namespace: str = ""
    tags: frozenset[str] = frozenset()
    deferred: bool = False


class ToolError(Exception):
    pass
