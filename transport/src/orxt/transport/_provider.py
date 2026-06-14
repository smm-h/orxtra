from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ._events import ContentBlock, Event, Usage


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    jitter: bool


class Provider(Protocol):
    def build_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        model: str,
    ) -> dict[str, Any]: ...

    def parse_response(self, response: dict[str, Any]) -> list[ContentBlock]: ...

    async def parse_stream(
        self,
        byte_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[Event]: ...

    def extract_usage(self, response: dict[str, Any]) -> Usage: ...
