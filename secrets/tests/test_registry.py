from __future__ import annotations

import pytest
from orxtra.secrets import SecretRegistry


class TestValidation:

    def test_empty_secret_value_raises(self) -> None:
        with pytest.raises(ValueError, match="A"):
            SecretRegistry({"A": ""})


class TestSubstitute:

    def test_single_placeholder(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        assert reg.substitute("{{secret:TOKEN}}") == "abc123"

    def test_multiple_placeholders(self) -> None:
        reg = SecretRegistry({"A": "val_a", "B": "val_b"})
        result = reg.substitute("{{secret:A}} and {{secret:B}}")
        assert result == "val_a and val_b"

    def test_unknown_placeholder_left_as_is(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        assert reg.substitute("{{secret:UNKNOWN}}") == "{{secret:UNKNOWN}}"

    def test_placeholder_in_larger_string(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        result = reg.substitute("Authorization: Bearer {{secret:TOKEN}}")
        assert result == "Authorization: Bearer abc123"

    def test_empty_registry_is_noop(self) -> None:
        reg = SecretRegistry({})
        text = "{{secret:TOKEN}} stays"
        assert reg.substitute(text) == text

    def test_empty_string_input(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        assert reg.substitute("") == ""

    def test_secret_name_with_special_chars(self) -> None:
        reg = SecretRegistry({"MY_API-KEY2": "secret_val"})
        result = reg.substitute("key={{secret:MY_API-KEY2}}")
        assert result == "key=secret_val"


class TestScrub:

    def test_single_secret_value(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        assert reg.scrub("abc123") == "{{secret:TOKEN}}"

    def test_multiple_secrets_in_one_string(self) -> None:
        reg = SecretRegistry({"A": "val_a", "B": "val_b"})
        result = reg.scrub("val_a and val_b")
        assert result == "{{secret:A}} and {{secret:B}}"

    def test_longest_first_scrubbing(self) -> None:
        reg = SecretRegistry({"SHORT": "abc", "LONG": "abcdef"})
        result = reg.scrub("abcdef")
        assert result == "{{secret:LONG}}"

    def test_overlapping_values_substring(self) -> None:
        reg = SecretRegistry({"SHORT": "abc", "LONG": "abcdef"})
        result = reg.scrub("abcdef and abc")
        assert result == "{{secret:LONG}} and {{secret:SHORT}}"

    def test_value_appearing_multiple_times(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        result = reg.scrub("abc123 then abc123")
        assert result == "{{secret:TOKEN}} then {{secret:TOKEN}}"

    def test_does_not_affect_placeholders_themselves(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        result = reg.scrub("{{secret:TOKEN}}")
        assert result == "{{secret:TOKEN}}"

    def test_empty_registry_is_noop(self) -> None:
        reg = SecretRegistry({})
        text = "nothing to scrub"
        assert reg.scrub(text) == text

    def test_empty_string_input(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        assert reg.scrub("") == ""

    def test_duplicate_secret_values(self) -> None:
        reg = SecretRegistry({"A": "same", "B": "same"})
        result = reg.scrub("same")
        assert result in ("{{secret:A}}", "{{secret:B}}")


class TestImmutability:

    def test_dict_is_copied(self) -> None:
        original = {"TOKEN": "abc123"}
        reg = SecretRegistry(original)
        original["TOKEN"] = "modified"  # noqa: S105
        original["NEW"] = "new_val"
        assert reg.substitute("{{secret:TOKEN}}") == "abc123"
        assert reg.substitute("{{secret:NEW}}") == "{{secret:NEW}}"


class TestDeepImmutability:

    def test_secrets_dict_is_immutable(self) -> None:
        registry = SecretRegistry({"TOKEN": "abc123"})
        with pytest.raises(TypeError):
            registry._secrets["new"] = "val"  # type: ignore[index]  # noqa: SLF001
        assert isinstance(registry._scrub_order, tuple)  # noqa: SLF001


class TestResolve:

    def test_resolve_known_name(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        assert reg.resolve("TOKEN") == "abc123"

    def test_resolve_unknown_name_raises_key_error(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        with pytest.raises(KeyError, match="Unknown secret 'MISSING'"):
            reg.resolve("MISSING")

    def test_resolve_error_lists_registered_names(self) -> None:
        reg = SecretRegistry({"A": "val_a", "B": "val_b"})
        with pytest.raises(KeyError, match=r"registered names: \['A', 'B'\]"):
            reg.resolve("C")

    def test_resolve_empty_registry(self) -> None:
        reg = SecretRegistry({})
        with pytest.raises(KeyError, match="Unknown secret 'X'"):
            reg.resolve("X")

    def test_resolve_all_secrets(self) -> None:
        secrets = {"A": "val_a", "B": "val_b", "C": "val_c"}
        reg = SecretRegistry(secrets)
        for name, value in secrets.items():
            assert reg.resolve(name) == value


class TestValidateReferences:

    def test_all_references_known(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123", "KEY": "xyz789"})
        result = reg.validate_references(
            "Use {{secret:TOKEN}} and {{secret:KEY}}"
        )
        assert result == {"TOKEN", "KEY"}

    def test_single_reference(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        result = reg.validate_references("auth: {{secret:TOKEN}}")
        assert result == {"TOKEN"}

    def test_no_references_returns_empty_set(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        result = reg.validate_references("no secrets here")
        assert result == set()

    def test_unknown_reference_raises_key_error(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        with pytest.raises(KeyError, match="Unknown secret references"):
            reg.validate_references("{{secret:MISSING}}")

    def test_unknown_reference_lists_all_unknowns(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        with pytest.raises(KeyError, match=r"\['BAD1', 'BAD2'\]"):
            reg.validate_references(
                "{{secret:BAD1}} and {{secret:BAD2}}"
            )

    def test_mixed_known_and_unknown_raises(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        with pytest.raises(KeyError, match="MISSING"):
            reg.validate_references(
                "{{secret:TOKEN}} and {{secret:MISSING}}"
            )

    def test_duplicate_references_deduplicated(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        result = reg.validate_references(
            "{{secret:TOKEN}} then {{secret:TOKEN}}"
        )
        assert result == {"TOKEN"}

    def test_empty_registry_with_references_raises(self) -> None:
        reg = SecretRegistry({})
        with pytest.raises(KeyError, match="Unknown secret references"):
            reg.validate_references("{{secret:X}}")

    def test_empty_text_returns_empty_set(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123"})
        result = reg.validate_references("")
        assert result == set()


class TestRoundTrip:

    def test_scrub_of_substituted_returns_original(self) -> None:
        reg = SecretRegistry({"TOKEN": "abc123", "KEY": "xyz789"})
        original = "Use {{secret:TOKEN}} and {{secret:KEY}}"
        substituted = reg.substitute(original)
        assert substituted == "Use abc123 and xyz789"
        scrubbed = reg.scrub(substituted)
        assert scrubbed == original

    def test_secret_value_looks_like_placeholder(self) -> None:
        reg = SecretRegistry({"A": "{{secret:B}}"})
        assert reg.substitute("{{secret:A}}") == "{{secret:B}}"
        assert reg.scrub("{{secret:B}}") == "{{secret:A}}"
