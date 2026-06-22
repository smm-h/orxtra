from __future__ import annotations

from orxtra.scheduler._services import ServiceInstance
from orxtra.scheduler._types import ServiceConfig


class TestServiceInstance:
    def test_creation(self) -> None:
        config = ServiceConfig(
            name="test-svc",
            start_command="echo start",
            stop_command="echo stop",
        )
        instance = ServiceInstance(config=config)
        assert instance.config.name == "test-svc"
        assert instance.process is None
        assert instance.port is None

    def test_creation_with_port(self) -> None:
        config = ServiceConfig(
            name="pg",
            start_command="pg_ctl start",
            stop_command="pg_ctl stop",
            port=5432,
        )
        instance = ServiceInstance(config=config, port=5432)
        assert instance.port == 5432
