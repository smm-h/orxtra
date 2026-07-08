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


def register_worker_commands(app: strictcli.App) -> None:
    """Register the ``worker`` command group on the orxtra CLI."""
    worker_group = app.group("worker", help="Worker process commands.")

    @worker_group.command(
        name="connect",
        help="Connect a native worker to a brain.",
    )
    @strictcli.flag(name="brain", type=str, help="Brain WebSocket URL.")
    @strictcli.flag(name="root", type=str, help="Project root directory.")
    @strictcli.flag(name="key", type=str, help="API key for authentication.")
    def cmd_worker_connect(
        *, brain: str, root: str, key: str, **_kwargs: object,
    ) -> None:
        worker = NativeWorker(
            brain_url=brain,
            root=Path(root),
            api_key=key,
        )
        asyncio.run(worker.run())

    @worker_group.command(
        name="docker",
        help="Run a worker inside a Docker container.",
    )
    @strictcli.flag(name="brain", type=str, help="Brain WebSocket URL.")
    @strictcli.flag(name="image", type=str, help="Docker image name.")
    @strictcli.flag(name="root", type=str, help="Project root directory.")
    @strictcli.flag(name="key", type=str, help="API key for authentication.")
    def cmd_worker_docker(
        *, brain: str, image: str, root: str, key: str, **_kwargs: object,
    ) -> None:
        worker = DockerWorker(
            brain_url=brain,
            image=image,
            root=Path(root),
            api_key=key,
        )
        asyncio.run(worker.run())
