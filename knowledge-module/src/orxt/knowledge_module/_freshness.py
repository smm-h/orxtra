from __future__ import annotations

import hashlib


class ContentHashCache:
    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def is_changed(self, key: str, content: str) -> bool:
        return self._hashes.get(key) != self._hash(content)

    def update(self, key: str, content: str) -> None:
        self._hashes[key] = self._hash(content)
