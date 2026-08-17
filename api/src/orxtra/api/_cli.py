"""CLI serve command for the HTTP compositor.

Registers a `serve` command group with the orxtra CLI. The command
starts the compositor server using fastware's granian backend.
"""

from __future__ import annotations

import json
import sys

import strictcli
from orxtra.api._lifecycle import ServerConfig, build_app

# Handlers absorb the app-level global flag values they do not name.
_ABSORBS_GLOBALS = strictcli.Forwarding(
    reason="absorbs app-level global flag values the handler does not name",
)

# Handler-side fallback for the optional --host flag. It cannot be a flag
# default: `serve` is mutating, and strictcli forbids a value default there.
_DEFAULT_HOST = "0.0.0.0"  # noqa: S104


def register_serve_command(app: strictcli.App) -> None:
    """Register the `serve` command on the given strictcli App."""

    @app.command(
        name="serve",
        help="Start the HTTP API server (MCP, A2A, AG-UI, native routes).",
        effect="mutating",
        forwarding=_ABSORBS_GLOBALS,
    )
    @strictcli.flag(
        name="port",
        type=int,
        help="TCP port number for the HTTP API server to listen on.",
        presence="required",
    )
    @strictcli.flag(
        name="host",
        type=str,
        help="Network interface address to bind the HTTP server to. "
        "Omitted, the server binds every interface (0.0.0.0).",
        presence="optional",
    )
    @strictcli.flag(
        name="secrets-env",
        type=str,
        help="JSON object mapping secret names to env var names for auth.",
        presence="optional",
    )
    def cmd_serve(
        _ctx: strictcli.Context, *,
        db: str | None,
        port: int,
        host: str | None,
        secrets_env: str | None,
        **_kwargs: object,
    ) -> None:
        if db is None:
            print("--db is required for serve", file=sys.stderr)
            sys.exit(1)

        # `serve` is a mutating command, so strictcli forbids a value default
        # on --host. The fallback lives here and is stated in the flag's help.
        bind_host = _DEFAULT_HOST if host is None else host

        parsed_secrets_env: dict[str, str] | None = None
        if secrets_env is not None:
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
            host=bind_host,
            secrets_env=parsed_secrets_env,
        )

        asgi_app = build_app(server_config)

        from fastware import serve

        serve(
            asgi_app,
            foreground=True,
            host=bind_host,
            port=port,
            name="ORXTRA",
        )
