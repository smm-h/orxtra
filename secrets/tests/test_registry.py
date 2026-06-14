from __future__ import annotations

import pytest
from orxt.secrets import SecretRegistry


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
