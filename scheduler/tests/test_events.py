from __future__ import annotations

import asyncio

import uuid6
from orxt.scheduler._events import EventRegistry


class TestEventRegistry:
    async def test_register_and_fire(self) -> None:
        registry = EventRegistry()
        task_id = uuid6.uuid7()
        registry.register("deploy_done", task_id)

        received: dict[str, object] | None = None

        async def listener() -> None:
            nonlocal received
            received = await registry.wait_for(
                "deploy_done", deadline_seconds=5.0,
            )

        task = asyncio.create_task(listener())
        await asyncio.sleep(0.01)
        await registry.fire(
            "deploy_done", {"status": "ok"},
        )
        await task
        assert received == {"status": "ok"}

    async def test_fire_with_no_listener(self) -> None:
        registry = EventRegistry()
        await registry.fire("no_one_listening", {"data": 1})

    async def test_wait_with_timeout_receives(self) -> None:
        registry = EventRegistry()

        async def delayed_fire() -> None:
            await asyncio.sleep(0.05)
            await registry.fire("evt", {"val": 42})

        fire_task = asyncio.create_task(delayed_fire())
        result = await registry.wait_for("evt", deadline_seconds=2.0)
        await fire_task
        assert result == {"val": 42}

    async def test_wait_timeout_expires(self) -> None:
        registry = EventRegistry()
        result = await registry.wait_for(
            "never_fires", deadline_seconds=0.05,
        )
        assert result is None

    async def test_multiple_waiters(self) -> None:
        registry = EventRegistry()
        results: list[dict[str, object] | None] = []

        async def waiter() -> None:
            r = await registry.wait_for(
                "multi", deadline_seconds=2.0,
            )
            results.append(r)

        tasks = [
            asyncio.create_task(waiter()) for _ in range(3)
        ]
        await asyncio.sleep(0.01)
        await registry.fire("multi", {"shared": True})
        await asyncio.gather(*tasks)
        assert len(results) == 3
        assert all(r == {"shared": True} for r in results)

    async def test_fire_then_wait_misses(self) -> None:
        registry = EventRegistry()
        await registry.fire("early", {"data": 1})
        result = await registry.wait_for(
            "early", deadline_seconds=0.05,
        )
        assert result is None
