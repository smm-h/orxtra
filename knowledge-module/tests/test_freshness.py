from __future__ import annotations

from orxt.knowledge_module._freshness import ContentHashCache


class TestContentHashCache:
    def test_new_content_is_changed(self) -> None:
        cache = ContentHashCache()
        assert cache.is_changed("key1", "content") is True

    def test_same_content_not_changed(self) -> None:
        cache = ContentHashCache()
        cache.update("key1", "content")
        assert cache.is_changed("key1", "content") is False

    def test_changed_content_detected(self) -> None:
        cache = ContentHashCache()
        cache.update("key1", "original")
        assert cache.is_changed("key1", "modified") is True

    def test_update_then_check_consistent(self) -> None:
        cache = ContentHashCache()
        cache.update("key1", "v1")
        assert cache.is_changed("key1", "v1") is False
        cache.update("key1", "v2")
        assert cache.is_changed("key1", "v2") is False
        assert cache.is_changed("key1", "v1") is True

    def test_different_keys_independent(self) -> None:
        cache = ContentHashCache()
        cache.update("key1", "content")
        assert cache.is_changed("key1", "content") is False
        assert cache.is_changed("key2", "content") is True
