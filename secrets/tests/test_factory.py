from __future__ import annotations

import pytest
from orxtra.secrets import SecretRegistry, create_secret_registry


class TestCreateSecretRegistry:

    def test_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN_VAR", "token_value")
        monkeypatch.setenv("MY_KEY_VAR", "key_value")
        reg = create_secret_registry({
            "TOKEN": "MY_TOKEN_VAR",
            "KEY": "MY_KEY_VAR",
        })
        assert isinstance(reg, SecretRegistry)
        assert reg.resolve("TOKEN") == "token_value"
        assert reg.resolve("KEY") == "key_value"

    def test_missing_env_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        with pytest.raises(KeyError, match="NONEXISTENT_VAR_XYZ"):
            create_secret_registry({"SECRET": "NONEXISTENT_VAR_XYZ"})

    def test_multiple_missing_env_vars_all_reported(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MISSING_A", raising=False)
        monkeypatch.delenv("MISSING_B", raising=False)
        with pytest.raises(KeyError, match=r"\['MISSING_A', 'MISSING_B'\]"):
            create_secret_registry({
                "SECRET_A": "MISSING_A",
                "SECRET_B": "MISSING_B",
            })

    def test_empty_mapping_returns_empty_registry(self) -> None:
        reg = create_secret_registry({})
        assert isinstance(reg, SecretRegistry)
        # No secrets registered, so substitute is a no-op
        assert reg.substitute("{{secret:X}}") == "{{secret:X}}"

    def test_substitute_works_through_factory(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("API_KEY_ENV", "real-api-key-value")
        reg = create_secret_registry({"API_KEY": "API_KEY_ENV"})
        result = reg.substitute("Authorization: Bearer {{secret:API_KEY}}")
        assert result == "Authorization: Bearer real-api-key-value"

    def test_scrub_works_through_factory(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("API_KEY_ENV", "real-api-key-value")
        reg = create_secret_registry({"API_KEY": "API_KEY_ENV"})
        result = reg.scrub("Authorization: Bearer real-api-key-value")
        assert result == "Authorization: Bearer {{secret:API_KEY}}"

    def test_empty_env_var_value_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EMPTY_VAR", "")
        # SecretRegistry.__init__ rejects empty values
        with pytest.raises(ValueError, match="empty value"):
            create_secret_registry({"SECRET": "EMPTY_VAR"})

    def test_partial_missing_reports_only_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PRESENT_VAR", "value")
        monkeypatch.delenv("ABSENT_VAR", raising=False)
        with pytest.raises(KeyError, match="ABSENT_VAR"):
            create_secret_registry({
                "GOOD": "PRESENT_VAR",
                "BAD": "ABSENT_VAR",
            })
