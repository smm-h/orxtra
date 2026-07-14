"""Tests for the API lifecycle -- schema verification at startup.

Verifies that the lifespan function calls verify_schema and propagates
SchemaError if the database schema is incomplete.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.api._lifecycle import (
    ServerConfig,
    _build_authenticator,
    build_app,
    lifespan,
)
from orxtra.services import SchemaError


def _storage_mock() -> MagicMock:
    """Build a PgPrincipalStorage stand-in with an awaitable mint_principal.

    The lifespan seeds the system principal via ``mint_principal``; the mock
    avoids a real DB round-trip against the mock pool.
    """
    storage = MagicMock()
    storage.mint_principal = AsyncMock()
    return storage


async def test_lifespan_calls_verify_schema() -> None:
    """Lifespan calls verify_schema after creating the pool."""
    config = ServerConfig(db_url="postgresql://test:test@localhost/test", port=8080)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with (
        patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "orxtra.services.verify_schema",
            new_callable=AsyncMock,
        ) as mock_verify,
        patch("orxtra.a2a.SkillRegistry"),
        patch("orxtra.a2a.build_agent_card"),
        patch("orxtra.services.get_capabilities", return_value=[]),
        patch("orxtra.identity.PgPrincipalStorage", return_value=_storage_mock()),
    ):
        async with lifespan(config):
            mock_verify.assert_awaited_once_with(mock_pool)


async def test_lifespan_wires_principal_storage_and_kind_registry() -> None:
    """The DispatchContext carries the principal_storage, kind_registry, and event_bus."""
    from orxtra.identity import KindRegistry

    storage = _storage_mock()
    config = ServerConfig(
        db_url="postgresql://test:test@localhost/test",
        port=8080,
        principal_kinds=("user",),
    )

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    mock_event_bus = MagicMock()
    mock_event_bus.close = AsyncMock()

    with (
        patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("orxtra.services.verify_schema", new_callable=AsyncMock),
        patch("orxtra.a2a.SkillRegistry"),
        patch("orxtra.a2a.build_agent_card"),
        patch("orxtra.services.get_capabilities", return_value=[]),
        patch("orxtra.identity.PgPrincipalStorage", return_value=storage),
        patch(
            "orxtra.trace.PgEventBus", return_value=mock_event_bus,
        ),
    ):
        async with lifespan(config) as compositor_config:
            ctx = compositor_config.dispatch_context
            assert ctx.principal_storage is storage
            assert isinstance(ctx.kind_registry, KindRegistry)
            # App-declared kind flows into the registry.
            assert "user" in ctx.kind_registry.kinds
            # Event bus is wired into the DispatchContext.
            assert ctx.event_bus is mock_event_bus


def test_principal_kinds_defaults_to_empty_tuple() -> None:
    config = ServerConfig(
        db_url="postgresql://test:test@localhost/test",
        port=8080,
    )
    assert config.principal_kinds == ()


async def test_lifespan_propagates_schema_error() -> None:
    """Lifespan propagates SchemaError from verify_schema."""
    config = ServerConfig(db_url="postgresql://test:test@localhost/test", port=8080)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    err_msg = (
        "Database schema is incomplete. "
        "Missing: tables.runs."
    )
    with (
        patch(
            "asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ),
        patch(
            "orxtra.services.verify_schema",
            new_callable=AsyncMock,
            side_effect=SchemaError(err_msg),
        ),
        pytest.raises(SchemaError, match=r"tables\.runs"),
    ):
        async with lifespan(config):
            pass  # Should not reach here


class TestSecretsEnvServerConfig:
    """Tests for the secrets_env field on ServerConfig."""

    def test_secrets_env_defaults_to_none(self) -> None:
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test",
            port=8080,
        )
        assert config.secrets_env is None

    def test_secrets_env_accepted(self) -> None:
        env = {"webhook_secret": "WEBHOOK_SECRET_VAR"}
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test",
            port=8080,
            secrets_env=env,
        )
        assert config.secrets_env == env


class TestBuildAuthenticator:
    """Tests for _build_authenticator -- constructs the full auth stack."""

    def test_constructs_authenticator_from_secrets_env(self) -> None:
        """_build_authenticator creates an Authenticator with three verifiers."""
        mock_pool = MagicMock()
        secrets_env = {"webhook_secret": "TEST_WEBHOOK_SECRET"}

        with patch.dict(os.environ, {"TEST_WEBHOOK_SECRET": "test-value"}):
            authenticator = _build_authenticator(mock_pool, secrets_env)

        from orxtra.auth import Authenticator

        assert isinstance(authenticator, Authenticator)
        # Three verifiers: bearer, api_key, hmac.
        assert set(authenticator._verifiers.keys()) == {
            "bearer",
            "api_key",
            "hmac",
        }

    def test_raises_on_missing_env_var(self) -> None:
        """_build_authenticator propagates KeyError for missing env vars."""
        mock_pool = MagicMock()
        secrets_env = {"webhook_secret": "NONEXISTENT_VAR_12345"}

        # Ensure the env var does not exist.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NONEXISTENT_VAR_12345", None)
            with pytest.raises(KeyError, match="NONEXISTENT_VAR_12345"):
                _build_authenticator(mock_pool, secrets_env)


class TestLifespanSecretsEnvWiring:
    """Verify lifespan constructs authenticator when secrets_env is set."""

    async def test_lifespan_constructs_authenticator_from_secrets_env(self) -> None:
        """When secrets_env is provided, lifespan auto-constructs the authenticator."""
        secrets_env = {"webhook_secret": "TEST_WH_SECRET"}
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test",
            port=8080,
            secrets_env=secrets_env,
        )

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        mock_authenticator = MagicMock()

        with (
            patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ),
            patch(
                "orxtra.services.verify_schema",
                new_callable=AsyncMock,
            ),
            patch("orxtra.a2a.SkillRegistry"),
            patch("orxtra.a2a.build_agent_card"),
            patch("orxtra.services.get_capabilities", return_value=[]),
            patch(
                "orxtra.api._lifecycle._build_authenticator",
                return_value=mock_authenticator,
            ) as mock_build,
            patch("orxtra.incoming.create_incoming_router") as mock_create_router,
            patch(
                "orxtra.identity.PgPrincipalStorage", return_value=_storage_mock()
            ),
        ):
            async with lifespan(config) as compositor_config:
                mock_build.assert_called_once_with(mock_pool, secrets_env)
                assert compositor_config.authenticator is mock_authenticator
                mock_create_router.assert_called_once()

    async def test_lifespan_no_auth_without_secrets_env(self) -> None:
        """Without secrets_env, authenticator stays None."""
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test",
            port=8080,
        )

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        with (
            patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ),
            patch(
                "orxtra.services.verify_schema",
                new_callable=AsyncMock,
            ),
            patch("orxtra.a2a.SkillRegistry"),
            patch("orxtra.a2a.build_agent_card"),
            patch("orxtra.services.get_capabilities", return_value=[]),
            patch(
                "orxtra.identity.PgPrincipalStorage", return_value=_storage_mock()
            ),
        ):
            async with lifespan(config) as compositor_config:
                assert compositor_config.authenticator is None

    async def test_explicit_authenticator_takes_precedence(self) -> None:
        """An explicit authenticator on ServerConfig is used even when
        secrets_env is also provided -- explicit wins over auto-construct.
        """
        explicit_auth = MagicMock()
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test",
            port=8080,
            authenticator=explicit_auth,
            secrets_env={"webhook_secret": "SHOULD_NOT_BE_USED"},
        )

        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        with (
            patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ),
            patch(
                "orxtra.services.verify_schema",
                new_callable=AsyncMock,
            ),
            patch("orxtra.a2a.SkillRegistry"),
            patch("orxtra.a2a.build_agent_card"),
            patch("orxtra.services.get_capabilities", return_value=[]),
            patch("orxtra.incoming.create_incoming_router"),
            patch(
                "orxtra.api._lifecycle._build_authenticator",
            ) as mock_build,
            patch(
                "orxtra.identity.PgPrincipalStorage", return_value=_storage_mock()
            ),
        ):
            async with lifespan(config) as compositor_config:
                assert compositor_config.authenticator is explicit_auth
                mock_build.assert_not_called()


class TestTeardownSymmetry:
    """Verify that failed-startup branches clean up symmetrically.

    build_app has two failed-startup paths: one where create_compositor
    raises, and one where the compositor's own ASGI lifespan reports
    startup failure. Both must tear down the compositor task and close
    the pool, matching the normal shutdown path.
    """

    @staticmethod
    async def _drive_lifespan(app: Any) -> list[dict[str, Any]]:
        """Drive the ASGI lifespan and collect sent messages."""
        to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return await to_app.get()

        async def send(msg: dict[str, Any]) -> None:
            sent.append(msg)

        await to_app.put({"type": "lifespan.startup"})
        scope: dict[str, Any] = {"type": "lifespan", "state": {}}
        await asyncio.wait_for(app(scope, receive, send), timeout=10)
        return sent

    async def test_create_compositor_failure_closes_pool(self) -> None:
        """When create_compositor raises, the pool is still closed and
        startup.failed is sent.
        """
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test", port=8080,
        )
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        with (
            patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ),
            patch("orxtra.services.verify_schema", new_callable=AsyncMock),
            patch("orxtra.a2a.SkillRegistry"),
            patch("orxtra.a2a.build_agent_card"),
            patch("orxtra.services.get_capabilities", return_value=[]),
            patch(
                "orxtra.identity.PgPrincipalStorage",
                return_value=_storage_mock(),
            ),
            patch(
                "orxtra.api._lifecycle.create_compositor",
                side_effect=RuntimeError("compositor boom"),
            ),
        ):
            app = build_app(config)
            sent = await self._drive_lifespan(app)

        assert any(m["type"] == "lifespan.startup.failed" for m in sent), (
            "startup.failed must be sent when create_compositor raises"
        )
        mock_pool.close.assert_awaited_once()

    async def test_compositor_lifespan_failure_closes_pool_and_cancels_task(
        self,
    ) -> None:
        """When the compositor's own lifespan reports startup failure, the
        compositor task is cancelled and the pool is closed.
        """
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test", port=8080,
        )
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        compositor_task_cancelled = False

        async def _fake_compositor(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            """A compositor whose lifespan reports startup failure."""
            nonlocal compositor_task_cancelled
            if scope["type"] == "lifespan":
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    # Report failure.
                    await send({
                        "type": "lifespan.startup.failed",
                        "message": "compositor failed",
                    })
                    # Remain alive until cancelled (simulating a real task).
                    try:
                        await asyncio.sleep(3600)
                    except asyncio.CancelledError:
                        compositor_task_cancelled = True
                        raise

        with (
            patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ),
            patch("orxtra.services.verify_schema", new_callable=AsyncMock),
            patch("orxtra.a2a.SkillRegistry"),
            patch("orxtra.a2a.build_agent_card"),
            patch("orxtra.services.get_capabilities", return_value=[]),
            patch(
                "orxtra.identity.PgPrincipalStorage",
                return_value=_storage_mock(),
            ),
            patch(
                "orxtra.api._lifecycle.create_compositor",
                return_value=_fake_compositor,
            ),
        ):
            app = build_app(config)
            sent = await self._drive_lifespan(app)

        assert any(m["type"] == "lifespan.startup.failed" for m in sent), (
            "startup.failed must be sent when compositor lifespan fails"
        )
        assert compositor_task_cancelled, (
            "the compositor task must be cancelled on startup failure, "
            "not leaked"
        )
        mock_pool.close.assert_awaited_once()

    async def test_verify_schema_failure_sends_startup_failed(self) -> None:
        """When verify_schema raises inside lifespan.__aenter__, the pool
        is closed by the lifespan's own finally block and startup.failed
        is sent. The lifespan CM must NOT receive __aexit__ (it was never
        entered).
        """
        config = ServerConfig(
            db_url="postgresql://test:test@localhost/test", port=8080,
        )
        mock_pool = AsyncMock()
        mock_pool.close = AsyncMock()

        with (
            patch(
                "asyncpg.create_pool",
                new_callable=AsyncMock,
                return_value=mock_pool,
            ),
            patch(
                "orxtra.services.verify_schema",
                new_callable=AsyncMock,
                side_effect=SchemaError("tables.runs missing"),
            ),
        ):
            app = build_app(config)
            sent = await self._drive_lifespan(app)

        assert any(m["type"] == "lifespan.startup.failed" for m in sent), (
            "startup.failed must be sent when verify_schema raises"
        )
        # The pool is closed by the lifespan CM's finally block (not by
        # _teardown, since lifespan_entered=False).
        mock_pool.close.assert_awaited_once()
