from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orxtra.session._session import Session
    from orxtra.transport import Event


class SyncSession:
    """Synchronous wrapper around Session.

    Collects all events from an async Session.send() call
    and returns them as a list. Intended for scripts and
    synchronous consumers.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def send(self, prompt: str, **kwargs: Any) -> list[Event]:
        events: list[Event] = []

        async def _collect() -> None:
            async for event in self._session.send(
                prompt, **kwargs,
            ):
                events.append(event)

        asyncio.run(_collect())
        return events

    @property
    def session_id(self) -> str | None:
        return self._session.session_id

    @property
    def total_input_tokens(self) -> int:
        return self._session.total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._session.total_output_tokens
