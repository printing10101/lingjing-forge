"""
Test Model Cache Implementation

Tests for:
- ModelCache singleton pattern
- LRU eviction strategy
- Thread safety
- Cache statistics tracking
- Memory management
- API endpoints
"""

import pytest
import threading
from unittest.mock import MagicMock

from app.ai.lnn.inference.model_cache import ModelCache


class TestModelCacheSingleton:
    """Test ModelCache singleton pattern"""

    def setup_method(self):
        ModelCache.reset_instance()

    def teardown_method(self):
        ModelCache.reset_instance()

    def test_singleton_instance(self):
        cache1 = ModelCache()
        cache2 = ModelCache()
        assert cache1 is cache2

    def test_singleton_with_different_params(self):
        cache1 = ModelCache(max_size=3)
        cache2 = ModelCache(max_size=5)
        assert cache1 is cache2
        assert cache1._max_size == 3

    def test_singleton_invalid_max_size(self):
        with pytest.raises(ValueError, match="max_size must be >= 1"):
            ModelCache.reset_instance()
            ModelCache(max_size=0)

    def test_singleton_negative_max_size(self):
        with pytest.raises(ValueError, match="max_size must be >= 1"):
            ModelCache.reset_instance()
            ModelCache(max_size=-1)


class TestModelCacheBasicOperations:
    """Test basic cache operations"""

    def setup_method(self):
        ModelCache.reset_instance()
        self.cache = ModelCache(max_size=3)

    def teardown_method(self):
        ModelCache.reset_instance()

    def test_put_and_get(self):
        model = MagicMock()
        self.cache.put("model_a", model, memory_size_bytes=1024)
        retrieved = self.cache.get("model_a")
        assert retrieved is model

    def test_get_nonexistent(self):
        result = self.cache.get("nonexistent")
        assert result is None

    def test_remove_existing(self):
        model = MagicMock()
        self.cache.put("model_a", model)
        assert self.cache.remove("model_a") is True
        assert self.cache.get("model_a") is None

    def test_remove_nonexistent(self):
        assert self.cache.remove("nonexistent") is False

    def test_contains(self):
        model = MagicMock()
        self.cache.put("model_a", model)
        assert self.cache.contains("model_a") is True
        assert self.cache.contains("model_b") is False

    def test_size(self):
        assert self.cache.size() == 0
        self.cache.put("model_a", MagicMock())
        assert self.cache.size() == 1
        self.cache.put("model_b", MagicMock())
        assert self.cache.size() == 2

    def test_clear(self):
        self.cache.put("model_a", MagicMock(), 1024)
        self.cache.put("model_b", MagicMock(), 2048)
        count, memory = self.cache.clear()
        assert count == 2
        assert memory == 3072
        assert self.cache.size() == 0


class TestModelCacheLRU:
    """Test LRU eviction strategy"""

    def setup_method(self):
        ModelCache.reset_instance()
        self.cache = ModelCache(max_size=3)

    def teardown_method(self):
        ModelCache.reset_instance()

    def test_lru_eviction(self):
        self.cache.put("model_a", MagicMock())
        self.cache.put("model_b", MagicMock())
        self.cache.put("model_c", MagicMock())
        self.cache.put("model_d", MagicMock())

        assert self.cache.size() == 3
        assert self.cache.contains("model_a") is False
        assert self.cache.contains("model_d") is True

    def test_lru_access_updates_order(self):
        self.cache.put("model_a", MagicMock())
        self.cache.put("model_b", MagicMock())
        self.cache.put("model_c", MagicMock())

        self.cache.get("model_a")

        self.cache.put("model_d", MagicMock())

        assert self.cache.contains("model_a") is True
        assert self.cache.contains("model_b") is False

    def test_lru_multiple_evictions(self):
        for i in range(10):
            self.cache.put(f"model_{i}", MagicMock())

        assert self.cache.size() == 3
        assert self.cache.contains("model_7") is True
        assert self.cache.contains("model_8") is True
        assert self.cache.contains("model_9") is True
        assert self.cache.contains("model_0") is False


class TestModelCacheStatistics:
    """Test cache statistics tracking"""

    def setup_method(self):
        ModelCache.reset_instance()
        self.cache = ModelCache(max_size=3)

    def teardown_method(self):
        ModelCache.reset_instance()

    def test_initial_stats(self):
        stats = self.cache.get_stats()
        assert stats["total_requests"] == 0
        assert stats["cache_hits"] == 0
        assert stats["cache_misses"] == 0
        assert stats["hit_rate"] == 0.0

    def test_hit_and_miss_stats(self):
        self.cache.put("model_a", MagicMock())

        self.cache.get("model_a")
        self.cache.get("model_a")

        self.cache.get("nonexistent")

        stats = self.cache.get_stats()
        assert stats["total_requests"] == 3
        assert stats["cache_hits"] == 2
        assert stats["cache_misses"] == 1
        assert abs(stats["hit_rate"] - 0.6667) < 0.001

    def test_cached_models_list(self):
        self.cache.put("model_a", MagicMock())
        self.cache.put("model_b", MagicMock())

        stats = self.cache.get_stats()
        assert set(stats["cached_models"]) == {"model_a", "model_b"}

    def test_model_details(self):
        self.cache.put("model_a", MagicMock(), memory_size_bytes=2048)
        self.cache.put("model_b", MagicMock(), memory_size_bytes=4096)

        stats = self.cache.get_stats()
        assert stats["total_cache_size_bytes"] == 6144
        assert stats["total_cache_size_mb"] > 0
        assert "model_a" in stats["model_details"]
        assert stats["model_details"]["model_a"]["memory_size_bytes"] == 2048

    def test_max_size_in_stats(self):
        stats = self.cache.get_stats()
        assert stats["max_size"] == 3


class TestModelCacheThreadSafety:
    """Test thread safety of cache operations"""

    def setup_method(self):
        ModelCache.reset_instance()
        self.cache = ModelCache(max_size=10)

    def teardown_method(self):
        ModelCache.reset_instance()

    def test_concurrent_puts(self):
        errors = []

        def put_model(name):
            try:
                self.cache.put(name, MagicMock(), 1024)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=put_model, args=(f"model_{i}",)) for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert self.cache.size() <= 10

    def test_concurrent_gets(self):
        self.cache.put("shared_model", MagicMock())
        results = []
        errors = []

        def get_model():
            try:
                result = self.cache.get("shared_model")
                results.append(result is not None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_model) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(results)
        assert len(results) == 50

    def test_concurrent_mixed_operations(self):
        errors = []

        def mixed_ops(thread_id):
            try:
                for i in range(20):
                    model_name = f"model_{thread_id}_{i}"
                    self.cache.put(model_name, MagicMock(), 1024)
                    self.cache.get(model_name)
                    self.cache.contains(model_name)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=mixed_ops, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert self.cache.size() <= 10


class TestModelCacheMemoryManagement:
    """Test memory tracking and management"""

    def setup_method(self):
        ModelCache.reset_instance()
        self.cache = ModelCache(max_size=3)

    def teardown_method(self):
        ModelCache.reset_instance()

    def test_memory_tracking(self):
        self.cache.put("model_a", MagicMock(), memory_size_bytes=1024 * 1024)
        self.cache.put("model_b", MagicMock(), memory_size_bytes=2 * 1024 * 1024)

        stats = self.cache.get_stats()
        assert stats["total_cache_size_bytes"] == 3 * 1024 * 1024
        assert stats["total_cache_size_mb"] == 3.0

    def test_memory_after_eviction(self):
        self.cache.put("model_a", MagicMock(), memory_size_bytes=1024)
        self.cache.put("model_b", MagicMock(), memory_size_bytes=2048)
        self.cache.put("model_c", MagicMock(), memory_size_bytes=4096)
        self.cache.put("model_d", MagicMock(), memory_size_bytes=8192)

        stats = self.cache.get_stats()
        assert stats["total_cache_size_bytes"] == 2048 + 4096 + 8192

    def test_memory_after_clear(self):
        self.cache.put("model_a", MagicMock(), memory_size_bytes=1024)
        self.cache.put("model_b", MagicMock(), memory_size_bytes=2048)

        count, memory = self.cache.clear()
        assert memory == 3072

        stats = self.cache.get_stats()
        assert stats["total_cache_size_bytes"] == 0


class TestModelCacheEdgeCases:
    """Test edge cases and boundary conditions"""

    def setup_method(self):
        ModelCache.reset_instance()

    def teardown_method(self):
        ModelCache.reset_instance()

    def test_cache_size_one(self):
        cache = ModelCache(max_size=1)
        cache.put("model_a", MagicMock())
        cache.put("model_b", MagicMock())

        assert cache.size() == 1
        assert cache.contains("model_a") is False
        assert cache.contains("model_b") is True

    def test_update_existing_model(self):
        cache = ModelCache(max_size=3)
        model_v1 = MagicMock()
        model_v2 = MagicMock()

        cache.put("model_a", model_v1, 1024)
        cache.put("model_a", model_v2, 2048)

        assert cache.size() == 1
        assert cache.get("model_a") is model_v2

        stats = cache.get_stats()
        assert stats["total_cache_size_bytes"] == 2048

    def test_is_full(self):
        cache = ModelCache(max_size=2)
        assert cache.is_full() is False

        cache.put("model_a", MagicMock())
        assert cache.is_full() is False

        cache.put("model_b", MagicMock())
        assert cache.is_full() is True

    def test_stats_after_clear(self):
        cache = ModelCache(max_size=3)
        cache.put("model_a", MagicMock())
        cache.get("model_a")
        cache.get("nonexistent")

        cache.clear()

        stats = cache.get_stats()
        assert stats["cache_hits"] == 1
        assert stats["cache_misses"] == 1
        assert stats["cached_models"] == []
