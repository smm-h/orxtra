"""CLI entry points for worker commands.

Provides ``register_worker_commands`` to wire worker subcommands
into the main orxtra CLI app.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import strictcli
from orxtra.worker._docker import DockerWorker
from orxtra.worker._native import NativeWorker

# Handlers absorb the app-level global flag values they do not name.
_ABSORBS_GLOBALS = strictcli.Forwarding(
    reason="absorbs app-level global flag values the handler does not name",
)


def register_worker_commands(app: strictcli.App) -> None:
    """Register the ``worker`` command group on the orxtra CLI."""
    worker_group = app.group(
        "worker",
        help="Manage remote worker processes that execute tool calls for the brain.",
    )

    @worker_group.command(
        name="connect",
        help="Run a native worker process that connects to a brain over WebSocket, "
        "authenticates with --key, registers the tool capabilities it can serve, and "
        "then executes the tool calls the brain routes to it against the project root "
        "given by --root. Runs until the connection closes or the process is stopped.",
        effect="mutating",
        forwarding=_ABSORBS_GLOBALS,
    )
    @strictcli.flag(
        name="brain",
        type=str,
        help="WebSocket URL of the brain to connect to (e.g. ws://host:port).",
    )
    @strictcli.flag(
        name="root",
        type=str,
        help="Filesystem path to the project root directory for tool execution.",
    )
    @strictcli.flag(
        name="key",
        type=str,
        help="API key used for authenticating with the brain server.",
    )
    def cmd_worker_connect(
        _ctx: strictcli.Context, *, brain: str, root: str, key: str, **_kwargs: object,
    ) -> None:
        worker = NativeWorker(
            brain_url=brain,
            root=Path(root),
            api_key=key,
        )
        asyncio.run(worker.run())

    @worker_group.command(
        name="docker",
        help="Run a worker inside a Docker container built from --image, connected to the "
        "brain at --brain and executing tool calls against the project root given by "
        "--root. Authenticates with --key exactly as the native worker does; the "
        "container is what isolates tool execution from the host filesystem.",
        effect="mutating",
        forwarding=_ABSORBS_GLOBALS,
    )
    @strictcli.flag(
        name="brain",
        type=str,
        help="WebSocket URL of the brain to connect to (e.g. ws://host:port).",
    )
    @strictcli.flag(
        name="image",
        type=str,
        help="Docker image name to use for the worker container.",
    )
    @strictcli.flag(
        name="root",
        type=str,
        help="Filesystem path to the project root directory for tool execution.",
    )
    @strictcli.flag(
        name="key",
        type=str,
        help="API key used for authenticating with the brain server.",
    )
    def cmd_worker_docker(
        _ctx: strictcli.Context,
        *,
        brain: str,
        image: str,
        root: str,
        key: str,
        **_kwargs: object,
    ) -> None:
        worker = DockerWorker(
            brain_url=brain,
            image=image,
            root=Path(root),
            api_key=key,
        )
        asyncio.run(worker.run())
