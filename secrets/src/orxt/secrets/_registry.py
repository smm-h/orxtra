from __future__ import annotations

import re


class SecretRegistry:

    _PATTERN = re.compile(r"\{\{secret:([A-Za-z0-9_-]+)\}\}")

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = dict(secrets)
        # Pre-compute scrub ordering: longest values first so overlapping
        # values (e.g., "abcdef" before "abc") are replaced correctly.
        self._scrub_order: list[tuple[str, str]] = sorted(
            self._secrets.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        )

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
