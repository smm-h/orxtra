from __future__ import annotations

import re
import types


class SecretRegistry:

    _PATTERN = re.compile(r"\{\{secret:([A-Za-z0-9_-]+)\}\}")

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = types.MappingProxyType(dict(secrets))
        empty = [name for name, value in self._secrets.items() if value == ""]
        if empty:
            msg = f"Secret {empty[0]!r} has an empty value"
            raise ValueError(msg)
        # Pre-compute scrub ordering: longest values first so overlapping
        # values (e.g., "abcdef" before "abc") are replaced correctly.
        self._scrub_order: tuple[tuple[str, str], ...] = tuple(sorted(
            self._secrets.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        ))

    def resolve(self, name: str) -> str:
        """Return the value for a registered secret name.

        Raises ``KeyError`` with a descriptive message when the name is
        not in the registry.  Used by HMAC verification and load-time
        secret validation.
        """
        try:
            return self._secrets[name]
        except KeyError:
            msg = (
                f"Unknown secret {name!r}; "
                f"registered names: {sorted(self._secrets)}"
            )
            raise KeyError(msg) from None

    def validate_references(self, text: str) -> set[str]:
        """Scan *text* for ``{{secret:NAME}}`` patterns and verify all
        referenced names exist in the registry.

        Returns the set of referenced secret names on success.
        Raises ``KeyError`` listing every unknown name if any reference
        is unresolvable.
        """
        found: set[str] = set()
        unknown: set[str] = set()
        for match in self._PATTERN.finditer(text):
            name = match.group(1)
            if name in self._secrets:
                found.add(name)
            else:
                unknown.add(name)
        if unknown:
            msg = (
                f"Unknown secret references: {sorted(unknown)}; "
                f"registered names: {sorted(self._secrets)}"
            )
            raise KeyError(msg)
        return found

    def substitute(self, text: str) -> str:
        """Replace {{secret:NAME}} placeholders with real values."""
        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in self._secrets:
                return self._secrets[name]
            return match.group(0)

        return self._PATTERN.sub(_replace, text)

    def scrub(self, text: str) -> str:
        """Replace real secret values with {{secret:NAME}} placeholders."""
        for name, value in self._scrub_order:
            text = text.replace(value, f"{{{{secret:{name}}}}}")
        return text
