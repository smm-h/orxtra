"""MCP notification sink for OverseerEvent delivery.

Translates domain events into MCP resource-updated notifications so
connected MCP clients are informed when data changes. Implements the
EventSink[OverseerEvent] protocol.

For standalone stdio deployment, the existing PG LISTEN/NOTIFY path
in MCPServer._start_event_listener remains the primary event delivery
mechanism. This sink is used for in-process event delivery when the
MCP server runs inside a compositor (e.g. fastware).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import AnyUrl

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.session import ServerSession

    from orxtra.protocols import OverseerEvent

logger = logging.getLogger(__name__)


def _get_active_sessions(mcp_app: FastMCP) -> list[ServerSession]:
    """Extract active server sessions from the FastMCP instance.

    The StreamableHTTPSessionManager tracks sessions internally.
    We reach into it to get active sessions for broadcasting
    notifications.
    """
    manager = mcp_app.session_manager
    sessions: list[ServerSession] = []
    # The session manager stores sessions in _session_map
    session_map = getattr(manager, "_session_map", None)
    if session_map is None:
        return sessions
    for _session_id, session_data in session_map.items():
        # Each entry may be a tuple/object containing the ServerSession
        session = getattr(session_data, "session", None)
        if session is not None:
            sessions.append(session)
    return sessions


class McpNotificationSink:
    """EventSink[OverseerEvent] that sends MCP resource-updated notifications.

    When events arrive, translates them to resource update notifications
    for connected MCP clients:
    - Task state changes -> orxtra://runs/{run_id}/tasks
    - Inbox item changes -> orxtra://runs/{run_id}/inbox
    - Run state changes  -> orxtra://runs/{run_id}

    The sink holds a reference to the FastMCP instance and broadcasts
    to all active sessions.
    """

    def __init__(self, mcp_app: FastMCP) -> None:
        self._mcp_app = mcp_app

    async def on_event(self, event: OverseerEvent) -> None:
        """Translate an OverseerEvent to MCP resource-updated notifications."""
        from orxtra.protocols._types._events import (
            BudgetExhausted,
            BudgetThresholdCrossed,
            HealthDegraded,
            InboxAnswered,
            InboxRejected,
            RunStarted,
            StructuralAdvisory,
            TaskEscalated,
            TaskFailed,
        )

        uris: list[str] = []

        if isinstance(event, RunStarted):
            # Run started -- no run_id on the event, notify the runs list
            uris.append("orxtra://runs")

        elif isinstance(event, (TaskFailed, TaskEscalated, StructuralAdvisory)):
            # Task-related events carry a task_id but we need run_id for the URI.
            # Since the event doesn't carry run_id directly, notify the
            # general runs list. The client can refresh individual runs.
            uris.append("orxtra://runs")

        elif isinstance(event, (BudgetThresholdCrossed, BudgetExhausted)):
            # Budget events carry workflow_id. Notify runs list.
            uris.append("orxtra://runs")

        elif isinstance(event, (InboxAnswered, InboxRejected)):
            # Inbox events carry item_id but not run_id.
            # Notify runs list so clients can refresh.
            uris.append("orxtra://runs")

        elif isinstance(event, HealthDegraded):
            # Health events are system-wide
            uris.append("orxtra://runs")

        if not uris:
            return

        sessions = _get_active_sessions(self._mcp_app)
        for session in sessions:
            for uri in uris:
                try:
                    await session.send_resource_updated(AnyUrl(uri))
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Failed to send resource update notification for %s",
                        uri,
                    )
