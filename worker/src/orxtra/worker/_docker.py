"""Docker worker: runs a native worker inside a Docker container.

Launches a container with the specified image, mounts the project
root, and passes brain connection details via environment variables.
The container runs the native worker internally.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

_logger = logging.getLogger("orxtra.worker.docker")

_CONTAINER_PROJECT_PATH = "/project"


class DockerNotFoundError(Exception):
    """Raised when the docker CLI is not available."""


class ContainerExitError(Exception):
    """Raised when the container exits with a non-zero status."""


class DockerWorker:
    """Runs a native worker inside a Docker container.

    The container gets the project root volume-mounted at /project
    and receives brain URL and API key via environment variables.
    """

    def __init__(
        self,
        brain_url: str,
        image: str,
        root: Path,
        api_key: str,
    ) -> None:
        self._brain_url = brain_url
        self._image = image
        self._root = root.resolve()
        self._api_key = api_key
        self._process: asyncio.subprocess.Process | None = None
        self._container_name: str | None = None

    async def run(self) -> None:
        """Start the Docker container and wait for it to exit."""
        docker_bin = shutil.which("docker")
        if docker_bin is None:
            msg = "docker CLI not found on PATH"
            raise DockerNotFoundError(msg)

        import uuid
        self._container_name = f"orxtra-worker-{uuid.uuid4().hex[:12]}"

        cmd = [
            docker_bin,
            "run",
            "--rm",
            "--name", self._container_name,
            "-v", f"{self._root}:{_CONTAINER_PROJECT_PATH}",
            "-e", f"ORXTRA_BRAIN_URL={self._brain_url}",
            "-e", f"ORXTRA_API_KEY={self._api_key}",
            "-e", f"ORXTRA_ROOT={_CONTAINER_PROJECT_PATH}",
            self._image,
        ]

        _logger.info(
            "Starting Docker container %s (image=%s, root=%s)",
            self._container_name, self._image, self._root,
        )

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await self._process.communicate()
        exit_code = self._process.returncode

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        if stdout:
            _logger.info("Container stdout: %s", stdout)
        if stderr:
            _logger.warning("Container stderr: %s", stderr)

        if exit_code != 0:
            msg = (
                f"Container {self._container_name} exited with code {exit_code}"
                f": {stderr}"
            )
            raise ContainerExitError(msg)

        _logger.info("Container %s exited cleanly", self._container_name)

    async def stop(self) -> None:
        """Stop the Docker container if running."""
        if self._container_name is None:
            return

        docker_bin = shutil.which("docker")
        if docker_bin is None:
            return

        _logger.info("Stopping container %s", self._container_name)
        proc = await asyncio.create_subprocess_exec(
            docker_bin, "stop", self._container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
