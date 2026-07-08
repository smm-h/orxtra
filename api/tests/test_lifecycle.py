"""Tests for the API lifecycle -- schema verification at startup.

Verifies that the lifespan function calls verify_schema and propagates
SchemaError if the database schema is incomplete.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.api._lifecycle import ServerConfig, _build_authenticator, lifespan
from orxtra.services import SchemaError


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
    ):
        async with lifespan(config):
            mock_verify.assert_awaited_once_with(mock_pool)


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
        ):
            async with lifespan(config) as compositor_config:
                assert compositor_config.authenticator is explicit_auth
                mock_build.assert_not_called()
