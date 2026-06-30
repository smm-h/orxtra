"""CLI serve command for the HTTP compositor.

Registers a `serve` command group with the orxtra CLI. The command
starts the compositor server using fastware's granian backend.
"""

from __future__ import annotations

import sys

import strictcli
from orxtra.api._lifecycle import ServerConfig, build_app


def register_serve_command(app: strictcli.App) -> None:
    """Register the `serve` command on the given strictcli App."""

    @app.command(name="serve", help="Start the HTTP API server.")
    @strictcli.flag(name="port", type=int, help="Port to listen on.")
    @strictcli.flag(
        name="host",
        type=str,
        help="Host to bind to.",
        default="0.0.0.0",  # noqa: S104
    )
    def cmd_serve(*, db: str, port: int, host: str, **_kwargs: object) -> None:
        if not db:
            print("--db is required for serve", file=sys.stderr)
            sys.exit(1)
        if not port:
            print("--port is required for serve", file=sys.stderr)
            sys.exit(1)

        server_config = ServerConfig(
            db_url=db,
            port=port,
            host=host,
        )

        asgi_app = build_app(server_config)

        from fastware import serve  # noqa: PLC0415

        serve(
            asgi_app,
            foreground=True,
            host=host,
            port=port,
            name="ORXTRA",
        )
