from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.scheduler._types import ServiceConfig


@dataclass
class ServiceInstance:
    config: ServiceConfig
    process: asyncio.subprocess.Process | None = None
    port: int | None = None
    _work_dir: Path | None = field(default=None, repr=False)


async def start_service(
    config: ServiceConfig, work_dir: Path,
) -> ServiceInstance:
    process = await asyncio.create_subprocess_shell(
        config.start_command,
        cwd=str(work_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    instance = ServiceInstance(
        config=config,
        process=process,
        port=config.port,
        _work_dir=work_dir,
    )
    if config.health_check_command is not None:
        deadline = (
            asyncio.get_event_loop().time()
            + config.ready_timeout
        )
        while asyncio.get_event_loop().time() < deadline:
            if await check_health(instance):
                return instance
            await asyncio.sleep(0.5)
        msg = (
            f"Service '{config.name}' did not become healthy"
            f" within {config.ready_timeout}s"
        )
        raise TimeoutError(msg)
    return instance


async def stop_service(instance: ServiceInstance) -> None:
    if (
        instance.process is not None
        and instance.process.returncode is None
    ):
        instance.process.terminate()
        await instance.process.wait()


async def check_health(instance: ServiceInstance) -> bool:
    if instance.config.health_check_command is None:
        return (
            instance.process is not None
            and instance.process.returncode is None
        )
    proc = await asyncio.create_subprocess_shell(
        instance.config.health_check_command,
        cwd=(
            str(instance._work_dir)  # noqa: SLF001
            if instance._work_dir  # noqa: SLF001
            else None
        ),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    code = await proc.wait()
    return code == 0
