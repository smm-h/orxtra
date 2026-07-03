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

    On success, attaches the Principal to scope["state"]["principal"].
    On failure (missing/invalid credential, disabled consumer), returns 401.
    Non-HTTP scopes (websocket, lifespan) are passed through unchanged.
    """

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        # Extract Authorization header from ASGI headers list.
        raw_token = _extract_bearer_token(scope.get("headers", []))

        if raw_token is None:
            await _send_error(send, 401, "Missing Authorization header")
            return

        try:
            principal = await authenticator.authenticate(raw_token)
        except Exception:  # noqa: BLE001
            await _send_error(send, 401, "Invalid or expired credential")
            return

        # Attach principal to scope state.
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["principal"] = principal

        await app(scope, receive, send)

    return middleware


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
