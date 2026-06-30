"""A2A JSON-RPC server backed by the orxtra capability dispatcher."""

# ruff: noqa: TC001,TC002,TC003,ARG002,PLC0415

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from orxtra.a2a._skills import SkillRegistry
from orxtra.a2a._state_bridge import TaskStateBridge
from starlette.applications import Starlette

from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers import RequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.types.a2a_pb2 import (
    ROLE_AGENT,
    TASK_STATE_CANCELED,
    TASK_STATE_SUBMITTED,
    TASK_STATE_WORKING,
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import UnsupportedOperationError

if TYPE_CHECKING:
    from orxtra.services._dispatcher import DispatchContext


class OrxtraRequestHandler(RequestHandler):
    """A2A RequestHandler backed by the orxtra capability dispatcher."""

    def __init__(
        self,
        dispatch_context: DispatchContext,
        skill_registry: SkillRegistry,
    ) -> None:
        self._ctx = dispatch_context
        self._skills = skill_registry
        self._state_bridge = TaskStateBridge()

    def _extract_text(self, message: Message) -> str:
        """Extract text content from an A2A message."""
        return "\n".join(
            part.text for part in message.parts if part.text
        )

    def _make_task(
        self,
        task_id: str,
        context_id: str,
        state: int,
        message_text: str | None = None,
    ) -> Task:
        """Construct an A2A Task protobuf."""
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=state),
        )
        if message_text is not None:
            task.history.append(
                Message(
                    role=ROLE_AGENT,
                    parts=[Part(text=message_text)],
                    task_id=task_id,
                    context_id=context_id,
                ),
            )
        return task

    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | Message:
        """Handle message/send: start a run or dispatch."""
        from orxtra.services import dispatch

        message = params.message
        text = self._extract_text(message)
        task_id = message.task_id or message.message_id
        context_id = message.context_id or task_id

        # Check metadata for skill routing
        skill_id = dict(message.metadata).get("skill_id", "")
        skill = (
            self._skills.get_skill(skill_id) if skill_id else None
        )

        if skill is not None:
            result = await dispatch(
                self._ctx,
                skill.capability_name,
                {"config_path": text}
                if skill.capability_name == "start_run"
                else {},
            )
            result_text = (
                str(result) if result is not None else "OK"
            )
            return self._make_task(
                task_id=task_id,
                context_id=context_id,
                state=TASK_STATE_SUBMITTED,
                message_text=result_text,
            )

        # Default: start_run with message text as config path
        try:
            result = await dispatch(
                self._ctx,
                "start_run",
                {"config_path": text.strip()},
            )
            result_text = (
                str(result)
                if result is not None
                else "Run started"
            )
        except Exception as exc:  # noqa: BLE001
            result_text = f"Error: {exc}"

        return self._make_task(
            task_id=task_id,
            context_id=context_id,
            state=TASK_STATE_SUBMITTED,
            message_text=result_text,
        )

    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event]:
        """Handle message/stream: stream task status updates."""
        task_or_message = await self.on_message_send(
            params, context,
        )

        if isinstance(task_or_message, Task):
            yield TaskStatusUpdateEvent(
                task_id=task_or_message.id,
                context_id=task_or_message.context_id,
                status=task_or_message.status,
            )
            yield task_or_message
        else:
            yield task_or_message

    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        """Handle tasks/get: retrieve task state from trace."""
        from orxtra.services import dispatch

        try:
            result = await dispatch(
                self._ctx,
                "get_run",
                {"run_id": params.id},
            )
        except Exception:  # noqa: BLE001
            return None

        if result is None:
            return None

        a2a_state = TASK_STATE_WORKING
        if hasattr(result, "state"):
            from orxtra.protocols import TaskState

            try:
                orxtra_state = TaskState(result.state)
            except ValueError:
                pass
            else:
                translation = self._state_bridge.translate(
                    orxtra_state,
                )
                if translation.a2a_state is not None:
                    a2a_state = translation.a2a_state

        return self._make_task(
            task_id=params.id,
            context_id=params.id,
            state=a2a_state,
        )

    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Handle tasks/list: list runs from trace."""
        from orxtra.services import dispatch

        try:
            result = await dispatch(
                self._ctx,
                "list_runs",
                {},
            )
        except Exception:  # noqa: BLE001
            return ListTasksResponse()

        tasks: list[Task] = []
        if isinstance(result, list):
            for run in result:
                run_id = (
                    str(run.run_id)
                    if hasattr(run, "run_id")
                    else str(run)
                )
                tasks.append(
                    self._make_task(
                        task_id=run_id,
                        context_id=run_id,
                        state=TASK_STATE_WORKING,
                    ),
                )

        return ListTasksResponse(tasks=tasks)

    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        """Handle tasks/cancel: abort a run."""
        from orxtra.services import dispatch

        try:
            await dispatch(
                self._ctx,
                "abort_run",
                {"run_id": params.id},
            )
        except Exception:  # noqa: BLE001
            return None

        return self._make_task(
            task_id=params.id,
            context_id=params.id,
            state=TASK_STATE_CANCELED,
        )

    # -- Not yet implemented --

    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError

    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        raise UnsupportedOperationError

    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        raise UnsupportedOperationError

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        raise UnsupportedOperationError

    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event]:
        raise UnsupportedOperationError
        yield

    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        raise UnsupportedOperationError


def create_app(
    dispatch_context: DispatchContext,
    agent_card: AgentCard,
    skill_registry: SkillRegistry,
    *,
    rpc_url: str = "/a2a",
) -> Starlette:
    """Create a Starlette ASGI app for A2A JSON-RPC."""
    handler = OrxtraRequestHandler(
        dispatch_context=dispatch_context,
        skill_registry=skill_registry,
    )

    routes = [
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(handler, rpc_url=rpc_url),
    ]

    return Starlette(routes=routes)
