from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class Fragment(BaseModel, frozen=True, strict=True, extra="forbid"):
    """A named, prioritized piece of prompt content."""

    name: str
    content: str
    priority: int = 0
    source: str


@runtime_checkable
class FragmentProvider(Protocol):
    """Seam for runtime-parameterized fragment sources.

    compose defines this protocol; trace-backed providers live above it
    and implement it. compose never imports trace.
    """

    def fragments(self, context: dict[str, Any]) -> list[Fragment]: ...
