"""step_import.step_cache 单元测试（LRU 缓存 / 哈希 / 清理 / 统计）。"""

from __future__ import annotations

import time

import pytest

from app.step_import.step_cache import (
    CacheEntry,
    StepCache,
    _StepCacheHolder,
    get_step_cache,
)

pytestmark = pytest.mark.unit


def _write_file(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding='utf-8')
    return str(p)


class TestComputeFileHash:
    def test_same_content_same_hash(self, tmp_path):
        p1 = _write_file(tmp_path, 'a.txt', 'hello')
        p2 = _write_file(tmp_path, 'b.txt', 'hello')
        assert StepCache.compute_file_hash(p1) == StepCache.compute_file_hash(p2)

    def test_different_content_different_hash(self, tmp_path):
        p1 = _write_file(tmp_path, 'a.txt', 'hello')
        p2 = _write_file(tmp_path, 'b.txt', 'world')
        assert StepCache.compute_file_hash(p1) != StepCache.compute_file_hash(p2)

    def test_hash_is_sha256_hex(self, tmp_path):
        p = _write_file(tmp_path, 'a.txt', 'hello')
        h = StepCache.compute_file_hash(p)
        assert len(h) == 64
        assert all(c in '0123456789abcdef' for c in h)


class TestCacheEntry:
    def test_age_and_idle(self):
        e = CacheEntry(file_hash='h', file_name='f', file_size=1, cached_at=time.time() - 10, last_accessed=time.time() - 5)
        assert e.age_seconds >= 9.9
        assert e.idle_seconds >= 4.9


class TestStepCache:
    def test_get_miss(self, tmp_path):
        cache = StepCache()
        p = _write_file(tmp_path, 'a.txt', 'hello')
        assert cache.get(p) is None
        assert cache.stats['miss_count'] == 1
        assert cache.stats['hit_count'] == 0

    def test_get_nonexistent_file(self, tmp_path):
        cache = StepCache()
        assert cache.get(tmp_path / 'nope.txt') is None
        assert cache.stats['miss_count'] == 0

    def test_put_and_get_hit(self, tmp_path):
        cache = StepCache()
        p = _write_file(tmp_path, 'a.txt', 'hello')
        entry = cache.put(p, stl_files=['a.stl'])
        assert entry.file_name == 'a.txt'
        assert entry.stl_files == ['a.stl']
        got = cache.get(p)
        assert got is not None
        assert got.file_hash == entry.file_hash
        assert cache.stats['hit_count'] == 1

    def test_lru_eviction(self, tmp_path):
        cache = StepCache(max_entries=2)
        p1 = _write_file(tmp_path, 'a.txt', '1')
        p2 = _write_file(tmp_path, 'b.txt', '2')
        p3 = _write_file(tmp_path, 'c.txt', '3')
        cache.put(p1)
        cache.put(p2)
        cache.put(p3)
        assert cache.size == 2
        assert cache.get(p1) is None
        assert cache.get(p2) is not None
        assert cache.get(p3) is not None

    def test_get_refreshes_lru(self, tmp_path):
        cache = StepCache(max_entries=2)
        p1 = _write_file(tmp_path, 'a.txt', '1')
        p2 = _write_file(tmp_path, 'b.txt', '2')
        p3 = _write_file(tmp_path, 'c.txt', '3')
        cache.put(p1)
        cache.put(p2)
        cache.get(p1)
        cache.put(p3)
        assert cache.get(p1) is not None
        assert cache.get(p2) is None
        assert cache.get(p3) is not None

    def test_invalidate(self, tmp_path):
        cache = StepCache()
        p = _write_file(tmp_path, 'a.txt', 'hello')
        cache.put(p)
        assert cache.invalidate(p) is True
        assert cache.get(p) is None
        assert cache.invalidate(p) is False

    def test_invalidate_nonexistent_file(self, tmp_path):
        cache = StepCache()
        assert cache.invalidate(tmp_path / 'nope.txt') is False

    def test_clear(self, tmp_path):
        cache = StepCache()
        p = _write_file(tmp_path, 'a.txt', 'hello')
        cache.put(p)
        cache.get(p)
        cache.clear()
        assert cache.size == 0
        assert cache.stats['hit_count'] == 0
        assert cache.stats['miss_count'] == 0

    def test_cleanup_expired(self, tmp_path):
        cache = StepCache(cleanup_interval=0, max_age_seconds=100.0, max_idle_seconds=100.0)
        p1 = _write_file(tmp_path, 'a.txt', '1')
        p2 = _write_file(tmp_path, 'b.txt', '2')
        cache.put(p1)
        cache.put(p2)
        h1 = StepCache.compute_file_hash(p1)
        cache._cache[h1].cached_at = time.time() - 99999
        cache._cache[h1].last_accessed = time.time() - 99999
        cache._maybe_cleanup()
        assert cache.get(p1) is None
        assert cache.get(p2) is not None

    def test_stats_hit_rate(self, tmp_path):
        cache = StepCache()
        p = _write_file(tmp_path, 'a.txt', 'hello')
        cache.get(p)
        cache.put(p)
        cache.get(p)
        stats = cache.stats
        assert stats['hit_count'] == 1
        assert stats['miss_count'] == 1
        assert stats['hit_rate'] == 0.5

    def test_size(self, tmp_path):
        cache = StepCache()
        p = _write_file(tmp_path, 'a.txt', 'hello')
        assert cache.size == 0
        cache.put(p)
        assert cache.size == 1


class TestSingleton:
    def test_get_step_cache_singleton(self):
        assert get_step_cache() is get_step_cache()

    def test_holder_reset(self):
        holder = _StepCacheHolder()
        c1 = holder.get()
        holder.reset()
        c2 = holder.get()
        assert c1 is not c2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
