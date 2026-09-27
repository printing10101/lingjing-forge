"""STEP解析缓存模块。

提供LRU缓存机制，对已解析的STEP模型数据进行临时存储，
避免重复解析相同的文件，提升系统响应性能。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    file_hash: str
    file_name: str
    file_size: int
    cached_at: float
    last_accessed: float
    stl_files: list[str] = field(default_factory=list)
    brep_files: list[str] = field(default_factory=list)
    parse_result_data: dict | None = None

    @property
    def age_seconds(self) -> float:
        return time.time() - self.cached_at

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_accessed


class StepCache:
    """STEP解析结果缓存。

    使用LRU(最近最少使用)策略管理缓存。
    缓存键为文件的SHA-256哈希值。
    """

    def __init__(
        self,
        max_entries: int = 20,
        max_age_seconds: float = 3600.0,
        max_idle_seconds: float = 1800.0,
        cleanup_interval: float = 300.0,
    ) -> None:
        self._max_entries = max_entries
        self._max_age_seconds = max_age_seconds
        self._max_idle_seconds = max_idle_seconds
        self._cleanup_interval = cleanup_interval

        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._last_cleanup = time.time()
        self._hit_count = 0
        self._miss_count = 0

        logger.info(
            "StepCache initialized: max_entries=%d, max_age=%.0fs, max_idle=%.0fs",
            max_entries,
            max_age_seconds,
            max_idle_seconds,
        )

    @staticmethod
    def compute_file_hash(file_path: str | Path) -> str:
        """计算文件的SHA-256哈希值。

        对大文件采用分块读取策略，避免内存溢出。
        """
        file_path = Path(file_path)
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()

    def get(self, file_path: str | Path) -> CacheEntry | None:
        """根据文件路径获取缓存条目。

        自动更新访问时间并触发定期清理。
        """
        file_path = Path(file_path)
        if not file_path.exists():
            return None

        file_hash = self.compute_file_hash(file_path)

        with self._lock:
            self._maybe_cleanup()

            if file_hash in self._cache:
                entry = self._cache[file_hash]
                self._cache.move_to_end(file_hash)
                entry.last_accessed = time.time()
                self._hit_count += 1
                logger.debug("缓存命中: %s", file_path.name)
                return entry

            self._miss_count += 1
            return None

    def put(
        self,
        file_path: str | Path,
        stl_files: list[str] | None = None,
        brep_files: list[str] | None = None,
        parse_result_data: dict | None = None,
    ) -> CacheEntry:
        """将解析结果存入缓存。

        若缓存已满，按LRU策略淘汰最久未使用的条目。
        """
        file_path = Path(file_path)
        file_hash = self.compute_file_hash(file_path)

        with self._lock:
            if file_hash in self._cache:
                self._cache.move_to_end(file_hash)

            entry = CacheEntry(
                file_hash=file_hash,
                file_name=file_path.name,
                file_size=file_path.stat().st_size,
                cached_at=time.time(),
                last_accessed=time.time(),
                stl_files=list(stl_files) if stl_files else [],
                brep_files=list(brep_files) if brep_files else [],
                parse_result_data=parse_result_data,
            )

            self._cache[file_hash] = entry
            self._cache.move_to_end(file_hash)

            while len(self._cache) > self._max_entries:
                oldest_key, _ = self._cache.popitem(last=False)
                logger.debug("缓存淘汰: %s", oldest_key[:16])

            return entry

    def invalidate(self, file_path: str | Path) -> bool:
        """失效指定文件的缓存。"""
        file_path = Path(file_path)
        if not file_path.exists():
            return False

        file_hash = self.compute_file_hash(file_path)
        with self._lock:
            if file_hash in self._cache:
                del self._cache[file_hash]
                logger.debug("缓存失效: %s", file_path.name)
                return True
            return False

    def clear(self) -> None:
        """清空所有缓存。"""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._hit_count = 0
            self._miss_count = 0
            logger.info("缓存已清空 (%d entries)", count)

    def _maybe_cleanup(self) -> None:
        """定期清理过期条目。"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = now

        expired = []
        for key, entry in self._cache.items():
            if entry.age_seconds > self._max_age_seconds:
                expired.append(key)
            elif entry.idle_seconds > self._max_idle_seconds:
                expired.append(key)

        for key in expired:
            del self._cache[key]

        if expired:
            logger.debug("清理过期缓存: %d entries", len(expired))

    @property
    def stats(self) -> dict:
        """获取缓存统计信息。"""
        with self._lock:
            total = self._hit_count + self._miss_count
            hit_rate = self._hit_count / total if total > 0 else 0.0
            return {
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "hit_count": self._hit_count,
                "miss_count": self._miss_count,
                "hit_rate": round(hit_rate, 4),
                "max_age_seconds": self._max_age_seconds,
                "max_idle_seconds": self._max_idle_seconds,
            }

    @property
    def size(self) -> int:
        """当前缓存条目数。"""
        return len(self._cache)


class _StepCacheHolder:
    """Thread-safe lazy holder for the :class:`StepCache` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: StepCache | None = None

    def get(self) -> StepCache:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = StepCache()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _StepCacheHolder()


def get_step_cache() -> StepCache:
    """获取共享的 :class:`StepCache` 单例；首次访问时懒初始化。

    Returns:
        :class:`StepCache` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_step_cache)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()
