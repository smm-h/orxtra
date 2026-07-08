"""EventSink implementations that bridge orxtra events to AG-UI."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from ag_ui.core import BaseEvent
from orxtra.agui._translator import AGUITranslator
from orxtra.protocols import OverseerEvent
from orxtra.transport import TransportEvent

# Async callback type: receives an AG-UI event and delivers it (e.g. to a
# Broadcaster or SSE stream).
AGUICallback = Callable[[BaseEvent], Coroutine[Any, Any, None]]


class AGUITransportSink:
    """EventSink[TransportEvent] that translates to AG-UI events.

    Each incoming TransportEvent is translated via the shared AGUITranslator
    and the resulting AG-UI events are forwarded to the output callback.
    """

    def __init__(
        self,
        translator: AGUITranslator,
        callback: AGUICallback,
    ) -> None:
        self._translator = translator
        self._callback = callback

    async def on_event(self, event: TransportEvent) -> None:
        agui_events = self._translator.translate_transport(event)
        for agui_event in agui_events:
            await self._callback(agui_event)


class AGUIOverseerSink:
    """EventSink[OverseerEvent] that translates to AG-UI events.

    Each incoming OverseerEvent is translated via the shared AGUITranslator
    and the resulting AG-UI events are forwarded to the output callback.
    """

    def __init__(
        self,
        translator: AGUITranslator,
        callback: AGUICallback,
    ) -> None:
        self._translator = translator
        self._callback = callback

    async def on_event(self, event: OverseerEvent) -> None:
        agui_events = self._translator.translate_overseer(event)
        for agui_event in agui_events:
            await self._callback(agui_event)
