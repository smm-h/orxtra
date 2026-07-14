from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orxtra.auth._authenticator import Authenticator

# ASGI type aliases
Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def auth_middleware(app: ASGIApp, authenticator: Authenticator) -> ASGIApp:
    """Pure ASGI middleware that authenticates requests via the Authorization header.

    On success, attaches the AuthContext to scope["state"]["auth_context"].
    On failure:
      - HTTP: returns 401 JSON error.
      - WebSocket: consumes websocket.connect, then sends websocket.close
        with code 4001. The inner app is never called.

    Non-HTTP/non-WebSocket scopes (lifespan etc.) are passed through unchanged.
    """

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await _handle_http(scope, receive, send)
        elif scope["type"] == "websocket":
            await _handle_websocket(scope, receive, send)
        else:
            # Lifespan and other scopes pass through.
            await app(scope, receive, send)

    async def _handle_http(
        scope: Scope, receive: Receive, send: Send,
    ) -> None:
        raw_token = _extract_bearer_token(scope.get("headers", []))

        if raw_token is None:
            await _send_error(send, 401, "Missing Authorization header")
            return

        try:
            auth_context = await authenticator.authenticate(raw_token)
        except Exception:  # noqa: BLE001
            await _send_error(send, 401, "Invalid or expired credential")
            return

        _attach_auth_context(scope, auth_context)
        await app(scope, receive, send)

    async def _handle_websocket(
        scope: Scope, receive: Receive, send: Send,
    ) -> None:
        # Headers are already on scope -- no message consumption needed.
        raw_token = _extract_bearer_token(scope.get("headers", []))

        if raw_token is None:
            await _reject_websocket(receive, send)
            return

        try:
            auth_context = await authenticator.authenticate(raw_token)
        except Exception:  # noqa: BLE001
            await _reject_websocket(receive, send)
            return

        _attach_auth_context(scope, auth_context)
        # Pass through with receive UNCONSUMED so the handler's
        # accept() can read websocket.connect normally.
        await app(scope, receive, send)

    return middleware


def _attach_auth_context(scope: Scope, auth_context: object) -> None:
    """Store the AuthContext in scope["state"]["auth_context"]."""
    if "state" not in scope:
        scope["state"] = {}
    scope["state"]["auth_context"] = auth_context


async def _reject_websocket(receive: Receive, send: Send) -> None:
    """Reject a WebSocket during the handshake.

    Consumes the websocket.connect message, then sends websocket.close
    with code 4001 (unauthorized). This is the correct ASGI sequence for
    rejecting a WebSocket before acceptance.
    """
    await receive()  # consume websocket.connect
    await send({"type": "websocket.close", "code": 4001})


def _extract_bearer_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Extract the bearer token from ASGI headers."""
    for name, value in headers:
        if name.lower() == b"authorization":
            decoded = value.decode("latin-1")
            if decoded.lower().startswith("bearer "):
                return decoded[7:].strip()
            # Non-bearer auth: treat the whole value as the credential.
            return decoded.strip()
    return None


async def _send_error(send: Send, status: int, detail: str) -> None:
    """Send a JSON error response."""
    body = json.dumps({"error": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })
