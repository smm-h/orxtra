"""CLI serve command for the HTTP compositor.

Registers a `serve` command group with the orxtra CLI. The command
starts the compositor server using fastware's granian backend.
"""

from __future__ import annotations

import json
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
    @strictcli.flag(
        name="secrets-env",
        type=str,
        help="JSON object mapping secret names to env var names for auth.",
        default="",
    )
    def cmd_serve(
        ctx, *,
        db: str,
        port: int,
        host: str,
        secrets_env: str,
        **_kwargs: object,
    ) -> None:
        if not db:
            print("--db is required for serve", file=sys.stderr)
            sys.exit(1)
        if not port:
            print("--port is required for serve", file=sys.stderr)
            sys.exit(1)

        parsed_secrets_env: dict[str, str] | None = None
        if secrets_env:
            try:
                parsed = json.loads(secrets_env)
            except json.JSONDecodeError as exc:
                print(
                    f"--secrets-env must be valid JSON: {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not isinstance(parsed, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in parsed.items()
            ):
                print(
                    "--secrets-env must be a JSON object with string keys and values",
                    file=sys.stderr,
                )
                sys.exit(1)
            parsed_secrets_env = parsed

        server_config = ServerConfig(
            db_url=db,
            port=port,
            host=host,
            secrets_env=parsed_secrets_env,
        )

        asgi_app = build_app(server_config)

        from fastware import serve

        serve(
            asgi_app,
            foreground=True,
            host=host,
            port=port,
            name="ORXTRA",
        )
